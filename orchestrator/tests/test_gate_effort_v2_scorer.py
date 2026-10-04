"""gate-effort@2: gate-effort@1, plus a shadow gate's first-run outcomes.

A shadow step (guardrail-gate@2 ``mode: shadow``) reports its first run's
verdict and findings like an enforced gate, and every fix and enforcement
outcome as ``not_applicable`` (never zero), so the first-run outcomes pair
across a shadow baseline and an enforced treatment. Enforced records score
exactly as under gate-effort@1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort import gate_effort_scorer
from raidar.scorers.gate_effort.records import RECORD_PATH
from raidar.scorers.gate_effort_v2 import GATE_EFFORT_V2, gate_effort_v2_scorer
from raidar.scorers.gate_effort_v2.scorer import SHADOW_REASON, SHADOW_STEP_NA, SHADOW_TOTAL_NA
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import (
    GUARDRAIL_STYLE_GATE,
    PASSED,
    REFUSED,
    _finding,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_guardrail_gate_v2_workflow import _agent, _run
from tests.test_guardrail_gate_v2_workflow import shims as shims  # noqa: F401 (fixture)
from tests.test_guardrail_gate_workflow import _reset_script as _reset_script  # noqa: F401

CP1, CP2 = "checkpoint_1", "checkpoint_2"


def _assess(*steps: StepInputs) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    return validate_result(GATE_EFFORT_V2, ctx, gate_effort_v2_scorer().assess(ctx))


def _shadow(trace: Path, output: str, *, findings: list[dict] | None = None) -> None:
    passed = output.startswith("guardrail-check: PASSED")
    _write_gate(
        trace,
        outputs=[output],
        termination="shadow_recorded",
        signal=None,
        reports={1: findings} if findings is not None else None,
    )
    record = json.loads((trace / RECORD_PATH).read_text())
    record |= {"mode": "shadow", "shadow": True, "shadow_verdict": passed}
    (trace / RECORD_PATH).write_text(json.dumps(record))


def test_a_shadow_trajectory_reports_first_run_outcomes_and_nothing_it_never_did(
    tmp_path: Path,
) -> None:
    first, second = _step(tmp_path, CP1), _step(tmp_path, CP2)
    _shadow(_trace(first), REFUSED, findings=[_finding("C901"), _finding("MYPY.no-untyped-def")])
    _shadow(_trace(second), PASSED)

    result = _assess(first, second)

    assert result.status is AssessStatus.OK
    assert _value(result, "first_pass_clean", CP1) == 0
    assert _value(result, "first_pass_clean", CP2) == 1
    assert _value(result, "first_run_blocking", CP1) == 2
    assert _value(result, "first_run_blocking.complexity", CP1) == 1
    assert _value(result, "first_run_blocking.typing", CP1) == 1
    assert (_value(result, "gate_runs", CP1), _value(result, "gate_error", CP1)) == (1, 0)
    assert _value(result, "first_pass_clean_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "gate_error_rate", TRAJECTORY_SUBJECT) == 0
    assert _value(result, "total_first_run_blocking", TRAJECTORY_SUBJECT) == 2
    observed = _values(result)
    for metric in SHADOW_STEP_NA:
        item = observed[(metric, CP1)]
        assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, SHADOW_REASON), metric
    for metric in SHADOW_TOTAL_NA:
        item = observed[(metric, TRAJECTORY_SUBJECT)]
        assert (item.status, item.value) == (AssessStatus.NOT_APPLICABLE, None), metric


def test_enforced_records_score_exactly_as_under_v1(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    ctx = AssessContext(problem="scb/forge", steps=(step,), config={})

    v1 = gate_effort_scorer().assess(ctx)
    v2 = gate_effort_v2_scorer().assess(ctx)

    assert v2.observations == v1.observations
    assert v2.evidence == v1.evidence
    assert v2.status is v1.status


def test_unreached_shadow_steps_stay_not_reached_and_mixed_modes_are_an_error(
    tmp_path: Path,
) -> None:
    first, unreached = _step(tmp_path, CP1), _step(tmp_path, CP2, reached=False)
    _shadow(_trace(first), PASSED)

    result = _assess(first, unreached)

    item = _values(result)[("first_pass_clean", CP2)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, "checkpoint not reached")
    enforced = _step(tmp_path / "mixed", CP2)
    _write_gate(_trace(enforced), outputs=[PASSED], termination="gate_clean", signal=True)
    mixed = _assess(first, enforced)
    assert mixed.status is AssessStatus.ERROR
    assert "mixes shadow and enforced" in (mixed.reason or "")


def test_an_arm_without_any_gate_is_not_applicable(tmp_path: Path) -> None:
    result = _assess(_step(tmp_path, CP1))

    assert result.status is AssessStatus.NOT_APPLICABLE
    assert {item.status for item in result.observations} == {AssessStatus.NOT_APPLICABLE}


def test_v2_is_registered_beside_v1_with_the_same_metrics() -> None:
    registry = default_scorer_registry()
    v1, v2 = registry.get("gate-effort@1"), registry.get("gate-effort@2")

    assert v2.load().manifest == GATE_EFFORT_V2
    assert [metric.id for metric in v2.manifest.metrics] == [
        metric.id for metric in v1.manifest.metrics
    ]
    assert v1.implementation_digest() != v2.implementation_digest()


def test_a_real_shadow_wrapper_run_scores_end_to_end(tmp_path: Path, shims: Path) -> None:  # noqa: F811
    agent, environment = _agent(tmp_path, shims, mode="shadow", command=GUARDRAIL_STYLE_GATE)
    _run(agent, environment)
    logs = tmp_path / "logs"
    step = StepInputs(
        step=CP1,
        reached=True,
        checkpoint=CP1,
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=logs,
        trace_digest="t",
        agent_result=None,
    )

    result = _assess(step)

    assert _value(result, "first_pass_clean", CP1) == 0
    assert _value(result, "first_run_blocking.complexity", CP1) == 1
    assert _value(result, "first_run_blocking.typing", CP1) == 1
    fix = _values(result)[("fix_rounds", CP1)]
    assert fix.status is AssessStatus.NOT_APPLICABLE
    with pytest.raises(KeyError):
        _values(result)[("fix_rounds", "checkpoint_9")]
