"""process-compliance@2: deterministic-only process checks (Jev parked 2026-10-04, F7).

A check the trace decides is ``ok`` exactly as under ``@1``; a check it cannot
decide is undetermined (``insufficient_evidence`` with a reason, never a
value, never a zero); nothing asks, binds or audits Jev.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.scorers import catalog
from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.keys import scorer_identity
from raidar.scorers.process_checks import DeterministicProcessComplianceScorer
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_compliance import ProcessComplianceScorer
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.manifest import Tier
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, Provenance
from raidar.scorers.sdk.validation import validate_result
from raidar.scorers.store import AssessmentStore
from tests.trace_trial_support import (
    TracedStep,
    agent_step,
    codex_exec,
    codex_patch,
    tool_call,
    traced_context,
    trajectory,
    write_traced_trial,
)

REF = "process-compliance@2"
CHECKS = ("tests_run_before_finish", "plan_written")
PLAN = tool_call("p0", "update_plan", {"plan": [{"step": "write"}]})
EDIT = codex_patch("e0", "/app/app/main.py")
UNDETERMINED = AssessStatus.INSUFFICIENT_EVIDENCE


def _ambiguous() -> dict:
    """A message-only plan and an unparsed in-place write after the test run."""
    return trajectory(
        agent_step(1, message="Plan:\n1. edit main\n2. run tests"),
        agent_step(2, codex_patch("e1", "/app/app/main.py")),
        agent_step(3, codex_exec("t1", "pytest -q")),
        agent_step(4, codex_exec("s1", "sed -i 's/a/b/' app/main.py")),
    )


def _decided_pass() -> dict:
    return trajectory(
        agent_step(1, PLAN), agent_step(2, EDIT), agent_step(3, codex_exec("t1", "pytest"))
    )


def _decided_fail() -> dict:
    return trajectory(agent_step(1, codex_exec("t1", "pytest")), agent_step(2, EDIT))


def _no_edit() -> dict:
    return trajectory(agent_step(1, codex_exec("r1", "cat app/main.py")))


def _ctx(tmp_path: Path, *traces: dict, **config: Any) -> AssessContext:
    steps = [TracedStep(f"checkpoint_{i}", trace=trace) for i, trace in enumerate(traces, 1)]
    return traced_context(tmp_path, steps, **config)


def _assess(ctx: AssessContext) -> AssessResult:
    scorer = DeterministicProcessComplianceScorer()
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def test_the_manifest_has_only_deterministic_metrics_and_no_jev_binding() -> None:
    assert PROCESS_COMPLIANCE_V2.ref == REF
    assert [metric.id for metric in PROCESS_COMPLIANCE_V2.metrics] == [
        *CHECKS,
        "compliance_rate",
    ]
    assert {metric.tier for metric in PROCESS_COMPLIANCE_V2.metrics} == {Tier.DETERMINISTIC}
    registration = default_scorer_registry().get(REF)
    assert registration.store_implementation is None
    assert registration.jev_policies == ()
    assert registration.jev_policy_bindings() is None
    assert scorer_identity(registration).jev_policies is None
    assert registration.load().manifest == PROCESS_COMPLIANCE_V2


def test_its_implementation_digest_covers_no_jev_code(monkeypatch: pytest.MonkeyPatch) -> None:
    modules: list[str] = []
    original = catalog.digest_json

    def spy(value: Any) -> str:
        if isinstance(value, dict) and "modules" in value:
            modules.extend(value["modules"])
        return original(value)

    monkeypatch.setattr(catalog, "digest_json", spy)
    default_scorer_registry().get(REF).implementation_digest()
    assert "raidar.scorers.process_checks.checks" in modules
    assert not [
        m for m in modules if m.startswith(("raidar.jev", "raidar.scorers.process_compliance"))
    ]


def test_undecidable_checks_are_undetermined_with_a_reason_never_zero(tmp_path: Path) -> None:
    result = _assess(_ctx(tmp_path, _ambiguous(), _decided_pass()))
    observed = _by_key(result)
    for check in CHECKS:
        item = observed[check, "checkpoint_1"]
        assert (item.status, item.value) == (UNDETERMINED, None)
        assert (item.reason or "").startswith("undetermined: ")
    # Only the decided step counts: two passes out of two decided checks.
    rate = observed["compliance_rate", "trajectory"]
    assert (rate.status, rate.value, rate.numerator, rate.denominator) == (
        AssessStatus.OK,
        1.0,
        2,
        2,
    )
    assert not [item for item in result.observations if item.metric.startswith("jev")]
    assert result.provenance == Provenance()


def test_a_trajectory_with_only_undetermined_checks_has_no_rate_not_a_zero(
    tmp_path: Path,
) -> None:
    result = _assess(_ctx(tmp_path, _ambiguous()))
    rate = _by_key(result)["compliance_rate", "trajectory"]
    assert (rate.status, rate.value) == (AssessStatus.NOT_APPLICABLE, None)
    assert result.status is AssessStatus.UNAVAILABLE


def test_decided_checks_match_the_dormant_version_exactly(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, _decided_pass(), _decided_fail(), _no_edit(), _ambiguous())
    current = _by_key(_assess(ctx))
    legacy = ProcessComplianceScorer()
    dormant = _by_key(validate_result(legacy.manifest, ctx, legacy.assess(ctx)))
    for key, item in current.items():
        before = dormant[key]
        assert (item.status, item.value, item.numerator, item.denominator) == (
            before.status,
            before.value,
            before.numerator,
            before.denominator,
        ), key
        if item.status is not UNDETERMINED:
            assert item.reason == before.reason, key
    assert current["tests_run_before_finish", "checkpoint_2"].value == 0.0
    assert current["plan_written", "checkpoint_3"].status is AssessStatus.NOT_APPLICABLE


def test_invalid_config_is_an_error(tmp_path: Path) -> None:
    result = _assess(_ctx(tmp_path, _decided_pass(), extra_test_patterns=["("]))
    assert result.status is AssessStatus.ERROR


def test_rescore_writes_no_jev_audit_and_nothing_is_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No key and no --jev-replay-only: nothing to answer, so nothing awaits.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    trial = write_traced_trial(
        tmp_path / "jobs" / "trial", [TracedStep("checkpoint_1", trace=_ambiguous())]
    )
    store = tmp_path / "store"
    args = ["assess", "--rescore", str(trial), "--scorer", REF, "--store", str(store)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    (entry,) = json.loads(result.output)["assessments"]
    stored = AssessmentStore(store).get(entry["key"])
    assert stored is not None
    assert stored.provisional is False
    assert stored.scorer.jev_policies is None
    assert not (store / "jev-audit").exists()
    item = _by_key(stored.result)["plan_written", "checkpoint_1"]
    assert (item.status, item.value) == (UNDETERMINED, None)

    replay = CliRunner().invoke(main, [*args, "--replay"])
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output)["counts"]["replayed_identical"] == 1
