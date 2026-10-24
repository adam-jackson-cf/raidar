"""gate-effort per gate mode and per source change.

A shadow step (guardrail-gate@2 ``mode: shadow``) reports its first run's verdict and findings
like an enforced gate, and every fix and enforcement outcome as ``not_applicable`` (never zero),
so the first-run outcomes pair across a shadow baseline and an enforced treatment. A trajectory
mixing the two modes is an error. The commit gate commits with ``--allow-empty``, so a step
whose agent changed no source file passes the gate's first run without checking anything new:
its ``first_pass_clean`` is ``not_applicable`` and left out of the rate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.scorers.gate_effort.shadow import SHADOW_REASON, SHADOW_STEP_NA, SHADOW_TOTAL_NA
from raidar.scorers.gate_effort.sources import NO_SOURCE_CHANGE, unchanged_steps
from raidar.scorers.sdk.context import StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus
from tests.gate_effort_support import (
    CP1,
    CP2,
    CP3,
    GUARDRAIL_STYLE_GATE,
    PASSED,
    REFUSED,
    _assess,
    _assess_context,
    _context,
    _enforced_sources,
    _finding,
    _shadow,
    _snapshot,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_guardrail_gate_workflow import _agent, _run
from tests.test_guardrail_gate_workflow import _reset_script as _reset_script  # noqa: F401
from tests.test_guardrail_gate_workflow import shims as shims  # noqa: F401 (fixture)


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


def test_a_no_edit_step_is_not_first_pass_clean(tmp_path: Path) -> None:
    steps = _enforced_sources(tmp_path, ["a = 1\n", "a = 1\n", "a = 2\n"])
    ctx = _context(*steps)

    result = _assess_context(ctx)

    assert unchanged_steps(ctx) == {CP2}
    item = _values(result)[("first_pass_clean", CP2)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)
    assert _value(result, "first_pass_clean", CP1) == 1
    assert _value(result, "first_pass_clean", CP3) == 1
    rate = _values(result)[("first_pass_clean_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.numerator, rate.denominator) == (1.0, 2.0, 2.0)
    assert _value(result, "first_run_blocking", CP2) == 0
    assert _value(result, "gate_runs", CP2) == 1


def test_the_first_step_compares_with_the_starter_only_when_there_is_one(tmp_path: Path) -> None:
    (step,) = _enforced_sources(tmp_path, ["a = 1\n"])
    assert unchanged_steps(_context(step)) == frozenset()
    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "forge.py").write_text("a = 1\n")

    result = _assess_context(_context(step, starter=starter))

    item = _values(result)[("first_pass_clean", CP1)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)


def test_an_edited_or_unsnapshotted_step_keeps_its_value(tmp_path: Path) -> None:
    first = _snapshot(tmp_path, _step(tmp_path, CP1), "a = 1\n")
    _write_gate(_trace(first), outputs=[PASSED], termination="gate_clean", signal=True)
    refused = _snapshot(tmp_path, _step(tmp_path, CP2), "a = 2\n")
    _write_gate(_trace(refused), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    bare = _step(tmp_path, CP3)
    _write_gate(_trace(bare), outputs=[PASSED], termination="gate_clean", signal=True)

    result = _assess_context(_context(first, refused, bare))

    assert _value(result, "first_pass_clean", CP2) == 0
    assert _value(result, "first_pass_clean", CP3) == 1


def test_a_shadow_no_edit_step_is_left_out_whatever_its_verdict(tmp_path: Path) -> None:
    first = _snapshot(tmp_path, _step(tmp_path, CP1), "a = 1\n")
    second = _snapshot(tmp_path, _step(tmp_path, CP2), "a = 1\n")
    _shadow(_trace(first), REFUSED, findings=[])
    _shadow(_trace(second), REFUSED, findings=[])

    result = _assess_context(_context(first, second))

    item = _values(result)[("first_pass_clean", CP2)]
    assert (item.status, item.reason) == (AssessStatus.NOT_APPLICABLE, NO_SOURCE_CHANGE)
    assert _values(result)[("fix_rounds", CP2)].status is AssessStatus.NOT_APPLICABLE
    rate = _values(result)[("first_pass_clean_rate", TRAJECTORY_SUBJECT)]
    assert (rate.value, rate.denominator) == (0.0, 1.0)
