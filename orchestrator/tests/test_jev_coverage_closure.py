"""Jev boundaries: transport deadline, cache races, redaction, promotion, CLI refusals."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests
import yaml
from click.testing import CliRunner

from raidar.cli import main
from raidar.judge import client as client_module
from raidar.judge.client import (
    JevClient,
    JevSuccess,
    RequestsTransport,
    TransportResponse,
    state_text,
)
from raidar.judge.protocol import JEV_PINNED_MODEL, JEV_STATE_CHAR_LIMIT, NoulQuestion
from raidar.scoring.framework.builtin_manifests import PROCESS_COMPLIANCE
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation, Provenance
from raidar.scoring.judge import rubric as rubric_module
from raidar.scoring.judge.audit import (
    AuditLog,
    DecisionStage,
    LabelRecord,
    ReviewerKind,
    SufficiencyLevel,
)
from raidar.scoring.judge.blind import derive_blind_labels, export_blind
from raidar.scoring.judge.cache import DecisionCache, decision_key
from raidar.scoring.judge.calibration import LabelImportError, import_labels
from raidar.scoring.judge.codebase import CodebaseIndex
from raidar.scoring.judge.gating import uncalibrated_observations, verdict_observations
from raidar.scoring.judge.ledger import SplitRecord
from raidar.scoring.judge.policies import PolicyMode, load_policy
from raidar.scoring.judge.promotion import (
    PromotionError,
    labelled_points,
    next_minor,
    plan_promotion,
    verify_promotion,
    write_promotion,
)
from raidar.scoring.judge.redaction import redact_value
from raidar.scoring.judge.rubric import RUBRIC_POLICY, RubricRunner
from raidar.scoring.judge.rubric_questions import (
    SCB_RUBRIC_FILE,
    QuestionSetError,
    Requirement,
    load_rubric_question_set,
)
from raidar.scoring.judge.symbols import SymbolKind, extract_symbols
from tests.jev_support import (
    SUFFICIENT,
    TEST_KEY,
    RecordedTransport,
    TransportTimeoutError,
    make_runtime,
    noul_answer,
    policy,
    shadow_policy_dir,
)

RUBRIC = "code-quality.rubric-smells"
QUESTIONS = {"stub": NoulQuestion(instructions="Is this a stub?")}
STATE = {"symbol": {"path": "app.py", "source": "def f(): ..."}}


# --- transport ----------------------------------------------------------------------------


class _Response:
    status_code = 200

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def iter_content(self, _size: int) -> list[bytes]:
        return self._chunks


def test_transport_joins_the_streamed_body_into_one_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(requests, "post", lambda *_a, **_k: _Response([b'{"a":', b"1}"]))
    response = RequestsTransport().post("http://x/decisions", headers={}, body=b"{}", timeout_s=5)
    assert response == TransportResponse(200, b'{"a":1}')


def test_a_body_that_arrives_after_the_deadline_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The worker stops reading once its own deadline has passed, whatever arrives.
    monkeypatch.setattr(requests, "post", lambda *_a, **_k: _Response([b"late"]))
    deadline = -1.0  # monotonic clock values are never negative
    with pytest.raises(TransportTimeoutError):
        RequestsTransport()._send("http://x", {}, b"{}", 5.0, deadline)


def test_a_request_still_pending_at_the_deadline_times_out_at_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    clock = iter([0.0])  # deadline is taken at t=0; every later reading is far past it

    def hang(*_args: object, **_kwargs: object) -> _Response:
        release.wait(5)  # released in `finally`, so the worker never outlives the test
        return _Response([b"late"])

    monkeypatch.setattr(requests, "post", hang)
    monkeypatch.setattr(
        client_module, "time", SimpleNamespace(monotonic=lambda: next(clock, 1_000.0))
    )
    try:
        with pytest.raises(TransportTimeoutError):
            RequestsTransport().post("http://x", headers={}, body=b"{}", timeout_s=30.0)
    finally:
        release.set()


# --- cache --------------------------------------------------------------------------------


def _success() -> JevSuccess:
    client = JevClient(TEST_KEY, transport=RecordedTransport())
    result = client.decide(STATE, QUESTIONS)
    assert isinstance(result, JevSuccess)
    return result


def test_storing_a_decision_twice_keeps_the_first_entry(tmp_path: Path) -> None:
    cache = DecisionCache(tmp_path / "cache")
    key = decision_key(STATE, QUESTIONS, JEV_PINNED_MODEL)
    first = cache.put(key, _success())
    again = cache.put(key, replace(_success(), cost_usd=9.0))
    assert again == first
    assert again.original_cost_usd != 9.0
    assert [path.name for path in cache.path_for(key).parent.iterdir()] == [f"{key}.json"]


def test_a_concurrent_writer_that_wins_the_link_race_is_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = DecisionCache(tmp_path / "cache")
    key = decision_key(STATE, QUESTIONS, JEV_PINNED_MODEL)
    real_link = os.link

    def lose_the_race(source: Path, target: Path) -> None:
        real_link(source, target)  # the other writer's identical entry lands first
        raise FileExistsError(target)

    monkeypatch.setattr(os, "link", lose_the_race)
    stored = cache.put(key, _success())
    assert cache.get(key) == stored


def test_a_link_failure_with_no_entry_on_file_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = DecisionCache(tmp_path / "cache")
    key = decision_key(STATE, QUESTIONS, JEV_PINNED_MODEL)

    def refuse(_source: Path, target: Path) -> None:
        raise FileExistsError(target)

    monkeypatch.setattr(os, "link", refuse)
    with pytest.raises(FileExistsError):
        cache.put(key, _success())
    assert cache.get(key) is None


# --- redaction, gating, audit, ledger -------------------------------------------------------


def test_redact_value_cleans_every_string_and_keeps_structure_and_finite_numbers() -> None:
    secret = "sk-or-v1-abcdef0123456789abcdef"
    value = {
        "n": None,
        "ok": True,
        "count": 3,
        "ratio": 0.5,
        "nan": float("nan"),
        "items": [f"key {secret}", ("Bearer abcdef0123456789",)],
        "nested": {"label": f"x {secret}"},
    }
    cleaned = redact_value(value, api_key=secret)
    assert cleaned["n"] is None
    assert cleaned["ok"] is True
    assert (cleaned["count"], cleaned["ratio"]) == (3, 0.5)
    assert cleaned["nan"] == "nan"  # non-finite numbers are text, so they are scanned too
    assert secret not in json.dumps(cleaned)
    assert cleaned["items"][1] == ["Bearer [REDACTED]"]
    assert list(cleaned["nested"]) == ["label"]  # keys are kept


def test_metrics_a_manifest_does_not_declare_never_reach_verdicts() -> None:
    result = AssessResult(
        status=AssessStatus.OK,
        observations=(
            Observation(metric="plan_written", subject="cp1", status=AssessStatus.OK, value=1.0),
            Observation(metric="ghost_metric", subject="cp1", status=AssessStatus.OK, value=1.0),
        ),
        provenance=Provenance(policy_modes={}),
    )
    assert {o.metric for o in verdict_observations(PROCESS_COMPLIANCE, result)} == {"plan_written"}
    assert {o.metric for o in uncalibrated_observations(PROCESS_COMPLIANCE, result)} == {
        "ghost_metric"
    }


def test_audit_refuses_unknown_labels_and_reads_no_decisions_for_an_unseen_policy(
    tmp_path: Path,
) -> None:
    audit = AuditLog.for_store(tmp_path)
    assert audit.decisions("never.seen") == []
    with pytest.raises(ValueError, match="unknown Jev label 'maybe'"):
        audit.append_label(
            LabelRecord(timestamp="t", request_id="r", policy="never.seen", label="maybe")
        )
    assert audit.labels("never.seen") == {}


def test_a_split_may_not_calibrate_and_evaluate_on_the_same_trial() -> None:
    with pytest.raises(ValueError, match=r"share trials \['t2'\]"):
        SplitRecord(
            timestamp="t",
            policy=RUBRIC,
            split_id="s",
            calibration=("t1", "t2"),
            evaluation=("t2", "t3"),
            reason="r",
        )


# --- rubric question set ----------------------------------------------------------------------


def test_question_set_refuses_unreadable_or_malformed_definitions(tmp_path: Path) -> None:
    with pytest.raises(QuestionSetError, match="missing.yaml"):
        load_rubric_question_set(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("a: [", encoding="utf-8")
    with pytest.raises(QuestionSetError, match="broken.yaml"):
        load_rubric_question_set(broken)


def test_question_set_refuses_overlays_for_criteria_the_pinned_file_lacks(tmp_path: Path) -> None:
    raw = yaml.safe_load(SCB_RUBRIC_FILE.read_text(encoding="utf-8"))
    raw["source"]["file"] = str(SCB_RUBRIC_FILE.parent / raw["source"]["file"])
    raw["criteria"]["no_such_criterion"] = {}
    definition = tmp_path / "rubric.yaml"
    definition.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(QuestionSetError, match="unknown or duplicate criteria .*no_such_criterion"):
        load_rubric_question_set(definition)


# --- rubric runner ----------------------------------------------------------------------------


def test_a_codebase_state_is_shrunk_until_it_fits_jevs_request_cap(tmp_path: Path) -> None:
    snapshot = tmp_path / "snap" / "checkpoint_1"
    snapshot.mkdir(parents=True)
    label = "x" * 2_000
    helpers = "".join(
        f"def helper_{i}(value, label='{label}'):\n    return value\n" for i in range(60)
    )
    (snapshot / "big.py").write_text(f"class Tiny:\n    pass\n\n\n{helpers}", encoding="utf-8")
    question_set = load_rubric_question_set()
    lonely = next(item for item in question_set.criteria if item.name == "lonely_interface")
    only_classes = replace(lonely, requires=Requirement.CODEBASE, kinds=(SymbolKind.CLASS,))
    roomy = replace(question_set, criteria=(only_classes,), codebase_chars=120_000)
    transport = RecordedTransport()
    runtime = make_runtime(
        tmp_path / "store", transport=transport, policies=(policy(RUBRIC_POLICY),)
    )
    (result,) = RubricRunner(runtime, roomy).run(
        [rubric_module.CheckpointSource("checkpoint_1", snapshot)]
    )
    (judgement,) = result.judgements
    assert judgement.status is rubric_module.JudgementStatus.ANSWERED
    assert judgement.sufficiency is SufficiencyLevel.PARTIAL
    (call,) = transport.calls
    assert len(state_text(call["body"]["state"])) <= JEV_STATE_CHAR_LIMIT


# --- codebase index -----------------------------------------------------------------------------


CODEBASE = """from abc import ABCMeta

LIMIT: int = 3


class Base(metaclass=ABCMeta):
    @property
    def size(self):
        return LIMIT


class Tagged(Base, flag=True):
    pass


def make():
    return factory()()
"""


def test_codebase_index_describes_metaclass_abstracts_annotated_names_and_chained_calls() -> None:
    index = CodebaseIndex({"m.py": CODEBASE})
    inventory = index.inventory()
    assert "class Base [abstract]; properties: size" in inventory
    assert "class Tagged(Base)" in inventory
    assert "[abstract]" not in inventory.split("Tagged")[1]
    module = next(s for s in extract_symbols("m.py", CODEBASE) if s.kind is SymbolKind.MODULE)
    assert "uses of LIMIT: 1 site(s)" in index.focus(module)  # LIMIT: int = 3 is a module name
    # factory()() names one call; the call whose callee is a call result has no name to match
    assert set(index.calls) == {"factory"}


def test_codebase_state_reports_clipping_and_unindexed_classes() -> None:
    index = CodebaseIndex({"m.py": CODEBASE})
    module = next(s for s in extract_symbols("m.py", CODEBASE) if s.kind is SymbolKind.MODULE)
    roomy = index.state(module, budget=10_000)
    assert not roomy.clipped
    assert roomy.as_state()["focus"] == index.focus(module)
    tight = index.state(module, budget=60)
    assert tight.clipped
    assert set(tight.as_state()) == {"focus", "inventory"}
    ghost = replace(module, qualname="Ghost", kind=SymbolKind.CLASS)
    assert index.focus(ghost) == "class Ghost was not indexed"


# --- blind labelling ------------------------------------------------------------------------------


def test_blind_labelling_skips_and_refuses_decisions_with_no_jev_answer(tmp_path: Path) -> None:
    answered = make_runtime(tmp_path, transport=RecordedTransport()).decide(
        RUBRIC, subject="a", state={"n": 1}, questions=QUESTIONS, sufficiency=SUFFICIENT
    )
    failed = make_runtime(tmp_path, replay_only=True).decide(
        RUBRIC, subject="b", state={"n": 2}, questions=QUESTIONS, sufficiency=SUFFICIENT
    )
    assert failed.record.stage is DecisionStage.JEV_ERROR
    audit = AuditLog.for_store(tmp_path)

    assert [row["request_id"] for row in export_blind(audit, RUBRIC)] == [
        answered.record.request_id
    ]

    def derive(row: dict[str, Any]) -> list[dict[str, Any]]:
        return derive_blind_labels(audit, RUBRIC, [row], reviewer=ReviewerKind.HUMAN)

    with pytest.raises(LabelImportError, match="row 0: no .* decision 'jevp_nope'"):
        derive({"request_id": "jevp_nope"})
    with pytest.raises(LabelImportError, match="has no Jev answer"):
        derive({"request_id": failed.record.request_id})
    with pytest.raises(LabelImportError, match="answers must be an object"):
        derive({"request_id": answered.record.request_id, "answers": "yes"})


# --- promotion ---

TRIALS = ("t1", "t2", "t3", "t4")


@pytest.fixture(scope="module")
def promotable_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """32 decisions over 4 trials; 30 carry a reference label, two are ``uncertain``."""
    root = tmp_path_factory.mktemp("promotable")
    scores = iter([0.85 if i % 4 == 0 else 0.15 for i in range(32)])
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(next(scores)))
    runtimes = {trial: make_runtime(root, transport=transport, trial=trial) for trial in TRIALS}
    rows = []
    for index in range(32):
        runtime = runtimes[TRIALS[index // 8]]
        record = runtime.decide(
            RUBRIC,
            subject=f"cp1:s{index}",
            state={"case": index},
            questions=QUESTIONS,
            sufficiency=SUFFICIENT,
        ).record
        label = "correct"
        if index == 1:
            label = "false_negative"  # a 0.15 decision the reviewer called positive
        if index >= 30:
            label = "uncertain"
        rows.append({"request_id": record.request_id, "label": label})
    import_labels(AuditLog.for_store(root), RUBRIC, rows)
    return root


def test_uncertain_labels_have_no_reference_class(promotable_store: Path) -> None:
    points = labelled_points(AuditLog.for_store(promotable_store), RUBRIC)
    assert len(points) == 30
    assert sum(point.positive for point in points) == 9


def test_a_promotion_ships_with_the_next_scorer_minor_and_verifies_against_it(
    promotable_store: Path,
) -> None:
    audit = AuditLog.for_store(promotable_store)
    plan = plan_promotion(policy(RUBRIC), audit, split=None, scorer_version="3")
    assert plan.ok, plan.failures
    assert plan.policy.mode is PolicyMode.ENFORCE
    assert plan.scorer_bump == {"scorer": "code-quality", "from": "3", "to": "3.1"}
    shipped = {"code-quality": "3.1"}
    assert verify_promotion(plan.policy, audit, scorer_versions=shipped) == ()
    behind = verify_promotion(plan.policy, audit, scorer_versions={"code-quality": "3"})
    assert any("scorer code-quality is at 3" in failure for failure in behind)
    absent = verify_promotion(plan.policy, audit, scorer_versions={})
    assert any("at no version" in failure for failure in absent)


def test_a_promotion_without_a_scorer_version_names_no_scorer_to_ship_with(
    promotable_store: Path,
) -> None:
    audit = AuditLog.for_store(promotable_store)
    plan = plan_promotion(policy(RUBRIC), audit, split=None, scorer_version=None)
    assert plan.ok
    assert plan.scorer_bump is None
    assert plan.policy.promotion is not None
    assert plan.policy.promotion.scorer is None
    assert verify_promotion(plan.policy, audit) == ()
    failures = verify_promotion(plan.policy, audit, scorer_versions={})
    assert failures == ("the promotion names no scorer version to ship with",)


def test_minor_versions_must_be_numeric() -> None:
    assert next_minor("1.2.5") == "1.3.5"
    with pytest.raises(PromotionError, match="cannot bump the minor version of '1.x'"):
        next_minor("1.x")


def _thin_audit(root: Path) -> AuditLog:
    """Four decisions over two trials: too few labels for the enforce gate."""
    scores = iter([0.9, 0.1, 0.9, 0.1])
    transport = RecordedTransport(answer_for=lambda _id, _q: noul_answer(next(scores)))
    rows = []
    for index, trial in enumerate(("a", "a", "b", "b")):
        record = make_runtime(root, transport=transport, trial=trial).decide(
            RUBRIC,
            subject=f"s{index}",
            state={"case": index},
            questions=QUESTIONS,
            sufficiency=SUFFICIENT,
        )
        rows.append({"request_id": record.record.request_id, "label": "correct"})
    audit = AuditLog.for_store(root)
    import_labels(audit, RUBRIC, rows)
    return audit


def test_a_promotion_that_fails_the_gate_stays_in_shadow_and_is_not_written(
    tmp_path: Path,
) -> None:
    audit = _thin_audit(tmp_path / "store")
    plan = plan_promotion(policy(RUBRIC), audit, split=None, scorer_version="1")
    assert not plan.ok
    assert plan.policy.mode is PolicyMode.SHADOW
    assert plan.policy.promotion is not None  # the evidence is shown, not enforced
    assert "promotion gate not met" in plan.failures[0]
    target = tmp_path / "policy.yaml"
    target.write_text("# keep\n", encoding="utf-8")
    with pytest.raises(PromotionError, match="does not verify"):
        write_promotion(target, plan)
    assert target.read_text(encoding="utf-8") == "# keep\n"


def test_a_verified_promotion_is_written_under_the_files_leading_comments(
    promotable_store: Path, tmp_path: Path
) -> None:
    plan = plan_promotion(
        policy(RUBRIC), AuditLog.for_store(promotable_store), split=None, scorer_version="3"
    )
    target = tmp_path / "policy.yaml"
    target.write_text("# Rubric policy\n# second line\n", encoding="utf-8")  # comments only
    write_promotion(target, plan)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# Rubric policy", "# second line"]
    promoted = f"# Promoted to enforce at version {plan.policy.version} by `make jev-promote`."
    assert lines[2] == promoted
    assert load_policy(target) == plan.policy


# --- CLI ---


def _invoke(*args: str):
    return CliRunner().invoke(main, ["jev", *args])


def test_policies_command_lists_every_policy_with_its_mode() -> None:
    result = _invoke("policies")
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert RUBRIC in {row["id"] for row in rows}
    assert {row["mode"] for row in rows} <= {"off", "shadow", "enforce"}


def test_shadow_rubric_prints_the_report_when_no_output_file_is_named(tmp_path: Path) -> None:
    (tmp_path / "snaps" / "checkpoint_1").mkdir(parents=True)
    result = _invoke(
        "shadow-rubric",
        "--snapshots",
        str(tmp_path / "snaps"),
        "--store",
        str(tmp_path / "store"),
        "--replay-only",
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert [c["checkpoint"] for c in report["checkpoints"]] == ["checkpoint_1"]
    assert report["checkpoints"][0]["python_files"] == 0


@pytest.mark.parametrize(
    "command", ["export-blind", "import-blind", "supersede-labels", "verify-promotion", "promote"]
)
def test_calibration_commands_refuse_an_unknown_policy(tmp_path: Path, command: str) -> None:
    store = tmp_path / "store"
    store.mkdir()
    answers = tmp_path / "answers.jsonl"
    answers.write_text("", encoding="utf-8")
    extra = {
        "export-blind": ["--output", str(tmp_path / "out.jsonl")],
        "import-blind": [str(answers)],
        "supersede-labels": ["--reason", "r", "--all-current"],
        "verify-promotion": [],
        "promote": [],
    }[command]
    result = _invoke(command, "--store", str(store), "--policy", "no.such-policy", *extra)
    assert result.exit_code == 1
    assert "no.such-policy" in result.output


def test_promote_refuses_a_split_that_shares_trials(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    policies = shadow_policy_dir(tmp_path / "policies")
    result = _invoke(
        "promote",
        "--store",
        str(store),
        "--policy",
        RUBRIC,
        "--policy-dir",
        str(policies),
        "--calibration",
        "t1 t2",
        "--evaluation",
        "t2",
    )
    assert result.exit_code == 1
    assert "share trials" in result.output


def test_verify_promotion_passes_once_the_scorer_bump_has_landed(
    promotable_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policies = shadow_policy_dir(tmp_path / "policies")
    base = ["--store", str(promotable_store), "--policy", RUBRIC, "--policy-dir", str(policies)]
    promoted = _invoke("promote", *base, "--allow-pending-scorer-bump")
    assert promoted.exit_code == 0, promoted.output
    written = load_policy(policies / f"{RUBRIC}.yaml")
    assert written.promotion is not None
    assert written.promotion.scorer is not None
    name, version = written.promotion.scorer.split("@", 1)
    monkeypatch.setattr("raidar.cli.jev_calibration.scorer_versions", lambda: {name: version})

    verified = _invoke("verify-promotion", *base)
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output) == {"policy": RUBRIC, "supported": True, "failures": []}
