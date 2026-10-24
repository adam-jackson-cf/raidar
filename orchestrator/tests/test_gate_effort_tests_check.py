"""gate-effort's tests check and the fix rounds' correctness.

- the tests check outcomes come from each run's report (``tests`` block); a ``TEST.*`` finding
  counts in the ``tests`` family;
- ``correctness_delta`` is the step's final case pass rate minus the pass rate of the workspace
  its first fix session started from, graded offline into the store; ungraded is ``unavailable``
  and the Assessment provisional; a shadow gate or a step without a fix round is
  ``not_applicable``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.gate_effort import GATE_EFFORT, gate_effort_scorer, gate_effort_scorer_for_store
from raidar.scoring.gate_effort.grades import NO_FIX_ROUND
from raidar.scoring.gate_effort.records import RECORD_PATH
from tests.gate_effort_support import (
    CP1,
    CP2,
    PASSED,
    REFUSED,
    _assess,
    _enforced,
    _finding,
    _graded,
    _report,
    _shadow,
    _step,
    _store_grade,
    _tests,
    _trace,
    _value,
    _values,
    _write_gate,
)


def _assess_awaiting(*steps: StepInputs, store: Path | None = None) -> tuple[AssessResult, bool]:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_scorer_for_store(store) if store else gate_effort_scorer()
    return validate_result(GATE_EFFORT, ctx, scorer.assess(ctx)), scorer.awaiting_answers()


def test_the_tests_check_and_the_fix_rounds_correctness_are_measured(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    first, second = _enforced(tmp_path)

    result, awaiting = _assess_awaiting(first, second, store=store)

    assert not awaiting
    assert _value(result, "first_run_blocking.tests", CP1) == 1
    assert _value(result, "first_run_blocking.other", CP1) == 0
    assert _value(result, "first_run_tests_passed", CP1) == 0
    assert _value(result, "first_run_tests_missing", CP1) == 1
    assert _values(result)[("first_run_coverage", CP1)].status is AssessStatus.NOT_APPLICABLE
    assert (
        _value(result, "tests_passed_at_step_end", CP1),
        _value(result, "coverage_at_step_end", CP1),
    ) == (1, 92.5)
    assert _value(result, "pre_fix_pass_rate", CP1) == 1.0
    assert _value(result, "correctness_delta", CP1) == pytest.approx(-0.25)
    gap = _values(result)[("correctness_delta", CP2)]
    assert (gap.status, gap.reason) == (AssessStatus.NOT_APPLICABLE, NO_FIX_ROUND)
    delta = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert (delta.value, delta.denominator) == (pytest.approx(-0.25), 1.0)
    assert _value(result, "tests_missing_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "first_run_tests_pass_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "mean_coverage_at_step_end", TRAJECTORY_SUBJECT) == pytest.approx(90.25)


def test_an_ungraded_pre_fix_snapshot_is_unavailable_and_the_assessment_provisional(
    tmp_path: Path,
) -> None:
    first, second = _enforced(tmp_path)

    result, awaiting = _assess_awaiting(first, second, store=tmp_path / "empty-store")

    assert awaiting
    item = _values(result)[("correctness_delta", CP1)]
    assert item.status is AssessStatus.UNAVAILABLE
    assert "not graded yet" in (item.reason or "")
    total = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert total.status is AssessStatus.UNAVAILABLE
    assert not _assess_awaiting(first, second)[1], "without a store nothing is awaited"


def test_a_shadow_gate_measures_the_code_not_the_fix_rounds(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=2, failed=2)
    _shadow(_trace(step), REFUSED, findings=[_finding("TEST.coverage-minimum")])
    record = json.loads((_trace(step) / RECORD_PATH).read_text())
    _report(_trace(step), 1, [_finding("TEST.coverage-minimum")], _tests(False, coverage=60.0))
    (_trace(step) / RECORD_PATH).write_text(json.dumps(record))

    result, _ = _assess_awaiting(step)

    assert _value(result, "first_run_tests_passed", CP1) == 0
    assert _value(result, "coverage_at_step_end", CP1) == 60.0
    for metric in ("correctness_delta", "pre_fix_pass_rate", "fix_rounds"):
        assert _values(result)[(metric, CP1)].status is AssessStatus.NOT_APPLICABLE
    total = _values(result)[("fix_round_correctness_delta", TRAJECTORY_SUBJECT)]
    assert total.status is AssessStatus.NOT_APPLICABLE


def test_a_pack_without_a_tests_check_leaves_its_outcomes_not_applicable(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _write_gate(_trace(step), outputs=[PASSED], termination="gate_clean", signal=True)

    result, _ = _assess_awaiting(step)

    item = _values(result)[("first_run_tests_passed", CP1)]
    assert item.status is AssessStatus.NOT_APPLICABLE
    assert _value(result, "first_pass_clean", CP1) == 1


def test_a_gate_run_summary_names_the_tests_family(tmp_path: Path) -> None:
    steps = _enforced(tmp_path)

    result = _assess(*steps)

    message = result.evidence[0].message
    assert message.endswith("refused, 2 blocking (complexity 1, tests 1)")
