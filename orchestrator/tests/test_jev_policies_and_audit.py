"""H2: versioned policies, complete audit, and shadow exclusion from verdicts."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.jev.audit import DecisionStage, InputSufficiency, SufficiencyLevel
from raidar.jev.calibration import (
    LabelImportError,
    import_labels,
    promotion_readiness,
)
from raidar.jev.gating import jev_policy_modes, uncalibrated_observations, verdict_observations
from raidar.jev.policies import (
    LABELS,
    CalibrationSplit,
    PolicyError,
    PolicyMode,
    PolicyRegistry,
    load_policy,
)
from raidar.jev.promotion import build_promotion, verify_promotion
from raidar.jev.protocol import NoulQuestion
from raidar.jev.runtime import RULE_INPUT_MISSING
from raidar.scorers.builtin_manifests import PROCESS_COMPLIANCE
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, Provenance
from tests.jev_support import SUFFICIENT, RecordedTransport, make_runtime, noul_answer, policy

RUBRIC = "code-quality.rubric-smells"
PROCESS = "process-compliance.residue"
QUESTIONS = {"stub": NoulQuestion(instructions="Is this a stub?")}


def _decide(runtime, policy_id: str, state: object, sufficiency=SUFFICIENT):
    return runtime.decide(
        policy_id, subject="cp1:s", state=state, questions=QUESTIONS, sufficiency=sufficiency
    )


def test_repo_policies_load_and_ship_in_shadow() -> None:
    registry = PolicyRegistry.from_dir()
    assert registry.ids() == (RUBRIC, PROCESS)
    assert set(registry.modes().values()) == {PolicyMode.SHADOW.value}


def test_enforce_requires_a_promotion_that_meets_the_gate(tmp_path: Path) -> None:
    base = {
        "id": "x.y",
        "version": "1",
        "description": "d",
        "mode": "enforce",
        "question_set": "q@1",
        "runtimes": ["python"],
    }
    promotion = {
        "threshold": 0.7,
        "labelled": 30,
        "minority": 5,
        "input_sufficiency": 0.8,
        "runtimes": ["python"],
        "split": {"calibration": ["t1"], "evaluation": ["t2"]},
        "regression_corpus": "b" * 64,
    }
    cases = {
        "missing.yaml": (base, "recorded promotion"),
        "few-labels.yaml": ({**base, "promotion": {**promotion, "labelled": 29}}, "29 labelled"),
        "few-minority.yaml": ({**base, "promotion": {**promotion, "minority": 4}}, "4 minority"),
        "thin-input.yaml": (
            {**base, "promotion": {**promotion, "input_sufficiency": 0.79}},
            "input sufficiency",
        ),
        "uncovered-runtime.yaml": (
            {**base, "runtimes": ["python", "go"], "promotion": promotion},
            r"\['go'\] have no calibration",
        ),
        "wildcard.yaml": ({**base, "runtimes": ["*"], "promotion": promotion}, "'\\*' policy"),
        "leaky-split.yaml": (
            {
                **base,
                "promotion": {**promotion, "split": {"calibration": ["t1"], "evaluation": ["t1"]}},
            },
            "share trials",
        ),
    }
    for name, (raw, message) in cases.items():
        path = tmp_path / name
        path.write_text(json.dumps(raw))
        with pytest.raises(PolicyError, match=message):
            load_policy(path)
    ok = tmp_path / "ok.yaml"
    ok.write_text(json.dumps({**base, "promotion": promotion}))
    assert load_policy(ok).mode is PolicyMode.ENFORCE


def test_run_overrides_may_lower_but_never_raise_a_mode() -> None:
    registry = PolicyRegistry((policy(RUBRIC), policy(PROCESS, PolicyMode.ENFORCE)))
    lowered = registry.with_overrides({RUBRIC: "off", PROCESS: "shadow"})
    assert lowered.modes() == {PROCESS: "shadow", RUBRIC: "off"}
    with pytest.raises(PolicyError, match="cannot raise shadow to enforce"):
        registry.with_overrides({RUBRIC: "enforce"})
    with pytest.raises(PolicyError, match="unknown mode"):
        registry.with_overrides({RUBRIC: "loud"})
    with pytest.raises(PolicyError, match="unknown Jev policy"):
        registry.with_overrides({"nope": "off"})
    with pytest.raises(PolicyError, match="duplicate"):
        PolicyRegistry((policy(RUBRIC), policy(RUBRIC)))


def test_every_decision_path_is_audited_exactly_once(tmp_path: Path) -> None:
    policies = (
        policy(RUBRIC),
        policy(PROCESS, PolicyMode.ENFORCE, threshold=0.6),
        policy("off.policy", PolicyMode.OFF),
    )
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(0.65))
    runtime = make_runtime(tmp_path, transport=transport, policies=policies)
    missing = InputSufficiency(level=SufficiencyLevel.MISSING, missing=("codebase",))
    outcomes = [
        _decide(runtime, "off.policy", {"a": 1}),
        _decide(runtime, RUBRIC, {"a": 2}, missing),
        _decide(runtime, RUBRIC, {"a": 3}),
        _decide(runtime, RUBRIC, {"a": 3}),
        _decide(runtime, PROCESS, {"a": 4}),
    ]
    settled = runtime.record_deterministic(
        PROCESS, subject="cp1:plan", rule="plan_file", verdict="complied"
    )
    transport.status = 500
    failed = _decide(runtime, RUBRIC, {"a": 5})

    audited = runtime.audit.decisions(RUBRIC) + runtime.audit.decisions(PROCESS)
    audited += runtime.audit.decisions("off.policy")
    assert len(audited) == runtime.totals.decisions == 7
    assert len({record.request_id for record in audited}) == 7
    off, insufficient, answered, replayed, enforced = (outcome.record for outcome in outcomes)
    assert (off.stage, off.verdict) == (DecisionStage.SKIPPED, "skipped")
    assert (insufficient.stage, insufficient.rule) == (DecisionStage.SKIPPED, RULE_INPUT_MISSING)
    assert (answered.verdict, answered.counts_toward_verdicts) == ("uncalibrated", False)
    assert (replayed.cache_hit, replayed.cost_usd) == (True, 0.0)
    assert (enforced.verdict, enforced.enforced, enforced.counts_toward_verdicts) == (
        "flag",
        True,
        True,
    )
    assert (failed.record.stage, failed.record.http_status) == (DecisionStage.JEV_ERROR, 500)
    assert failed.record.counts_toward_verdicts is False
    assert (settled.enforced, settled.counts_toward_verdicts) == (True, True)
    assert len(transport.calls) == 3
    _assert_jev_lab_readable(runtime.audit.root, RUBRIC, 4)


def _assert_jev_lab_readable(audit_root: Path, policy_id: str, decisions: int) -> None:
    """The jev.ts layout and the jev-lab schema: dated files per policy, optional fields absent."""
    files = sorted((audit_root / policy_id).glob("*.jsonl"))
    assert files
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", path.name) for path in files)
    rows = [json.loads(line) for path in files for line in path.read_text().splitlines()]
    assert len(rows) == decisions
    for row in rows:
        assert {"schema", "type", "requestId", "policy", "stage", "subject", "verdict"} <= set(row)
        assert {"labels", "enforced", "countsTowardVerdicts"} <= set(row)
        assert None not in row.values(), row


def _result(modes: dict[str, str]) -> AssessResult:
    observations = (
        Observation(metric="plan_written", subject="cp1", status=AssessStatus.OK, value=1.0),
        Observation(
            metric="jev_ambiguous_compliance", subject="cp1", status=AssessStatus.OK, value=0.8
        ),
    )
    return AssessResult(
        status=AssessStatus.OK,
        observations=observations,
        provenance=Provenance(policy_modes=modes),
    )


@pytest.mark.parametrize(
    ("modes", "counted"),
    [
        ({"jev_ambiguous_compliance": "shadow"}, False),
        ({"jev_ambiguous_compliance": "off"}, False),
        ({}, False),
        ({"jev_ambiguous_compliance": "enforce"}, True),
    ],
)
def test_only_enforced_jev_observations_reach_verdicts(
    modes: dict[str, str], counted: bool
) -> None:
    result = _result(modes)
    kept = {item.metric for item in verdict_observations(PROCESS_COMPLIANCE, result)}
    shown = {item.metric for item in uncalibrated_observations(PROCESS_COMPLIANCE, result)}
    assert "plan_written" in kept
    assert ("jev_ambiguous_compliance" in kept) is counted
    assert ("jev_ambiguous_compliance" in shown) is not counted


def test_policy_modes_for_metrics_follow_the_effective_registry() -> None:
    registry = PolicyRegistry((policy(PROCESS),)).with_overrides({PROCESS: "off"})
    modes = jev_policy_modes({"jev_ambiguous_compliance": PROCESS}, registry)
    assert modes == {"jev_ambiguous_compliance": "off"}


def _labelled_store(tmp_path: Path, answers: list[float], trials: tuple[str, ...] = ("t1",)):
    probabilities = iter(answers)
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(next(probabilities)))
    records = []
    for index in range(len(answers)):
        runtime = make_runtime(
            tmp_path, transport=transport, trial=trials[index * len(trials) // len(answers)]
        )
        records.append(_decide(runtime, RUBRIC, {"case": index}).record)
    return runtime, records


def test_label_import_validates_and_readiness_applies_the_promotion_gate(tmp_path: Path) -> None:
    runtime, records = _labelled_store(tmp_path, [0.9] * 6 + [0.1] * 26)
    with pytest.raises(LabelImportError, match="not one of"):
        import_labels(runtime.audit, RUBRIC, [{"request_id": records[0].request_id, "label": "ok"}])
    with pytest.raises(LabelImportError, match="no code-quality.rubric-smells decision"):
        import_labels(runtime.audit, RUBRIC, [{"request_id": "jevp_unknown", "label": "correct"}])
    twice = [{"request_id": records[0].request_id, "label": label} for label in LABELS[:2]]
    with pytest.raises(LabelImportError, match="already has a label"):
        import_labels(runtime.audit, RUBRIC, twice)
    assert runtime.audit.labels(RUBRIC) == {}

    rows = [{"request_id": record.request_id, "label": "correct"} for record in records[:31]]
    rows[0]["label"] = "uncertain"
    assert import_labels(runtime.audit, RUBRIC, rows) == 31
    readiness = promotion_readiness(runtime.audit, RUBRIC)
    assert (readiness.labelled, readiness.reference_positive, readiness.minority) == (30, 5, 5)
    assert readiness.ready, readiness.failures

    relabel = [{"request_id": records[1].request_id, "label": "false_positive"}]
    with pytest.raises(LabelImportError, match="already has a label"):
        import_labels(runtime.audit, RUBRIC, relabel)
    assert promotion_readiness(runtime.audit, RUBRIC) == readiness


def test_promotion_claims_are_verified_against_the_split_audit(tmp_path: Path) -> None:
    runtime, records = _labelled_store(tmp_path, [0.9] * 6 + [0.1] * 26, ("t1", "t2"))
    rows = [{"request_id": record.request_id, "label": "correct"} for record in records]
    import_labels(runtime.audit, RUBRIC, rows)
    claimed = policy(RUBRIC, PolicyMode.ENFORCE)  # claims 40 labelled, 8 minority, 0.9 input
    split = CalibrationSplit(calibration=("t1",), evaluation=("t2",))
    assert claimed.promotion is not None
    built = build_promotion(claimed, runtime.audit, split, scorer_version="1")
    assert (built.labelled, built.minority, built.input_sufficiency) == (32, 6, 1.0)
    honest = claimed.model_copy(update={"promotion": built})
    assert verify_promotion(honest, runtime.audit) == ()
    # A hand-edited number no longer matches what the audit re-derives.
    edited = honest.model_copy(update={"promotion": built.model_copy(update={"threshold": 0.7})})
    assert verify_promotion(edited, runtime.audit) == (
        "recorded threshold 0.7 is not the audited 0.5",
    )
    failures = verify_promotion(claimed, runtime.audit)
    assert any("outside the split: ['t1', 't2']" in failure for failure in failures)
    assert any("recorded labelled 40 exceeds the audited 0" in failure for failure in failures)
    assert verify_promotion(policy(RUBRIC), runtime.audit) == (
        f"policy {RUBRIC} has no recorded promotion",
    )
    shipped = CliRunner().invoke(
        main, ["jev", "verify-promotion", "--store", str(tmp_path), "--policy", RUBRIC]
    )
    assert shipped.exit_code == 1
    assert json.loads(shipped.output)["supported"] is False


def test_import_labels_cli_reports_readiness(tmp_path: Path) -> None:
    runtime, records = _labelled_store(tmp_path, [0.9, 0.1])
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        "\n".join(json.dumps({"request_id": r.request_id, "label": "correct"}) for r in records)
    )
    result = CliRunner().invoke(
        main, ["jev", "import-labels", "--store", str(tmp_path), "--policy", RUBRIC, str(labels)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert (report["imported"], report["ready_for_enforce"]) == (2, False)
    bad = CliRunner().invoke(
        main, ["jev", "import-labels", "--store", str(tmp_path), "--policy", "nope", str(labels)]
    )
    assert bad.exit_code == 1
    assert "unknown Jev policy" in bad.output
