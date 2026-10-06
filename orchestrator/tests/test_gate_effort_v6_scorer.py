"""gate-effort@6: blocking counts that compare across gate modes (smoke-04 findings O3-O5).

- every @5 observation is kept when no run has more than one ``TEST.failed``;
- a failed tests check counts once in every blocking count; the failing tests
  are their own detail (``first_run_failed_tests``);
- ``new_first_run_blocking`` telescopes a shadow gate's re-counted debt to the
  final tree, and ``final_step_end_blocking`` is that tree in both modes;
- the mypy ``no-untyped-call`` cascade is reported beside the typing family;
- failing tests and a missed coverage minimum are separate first-run outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort_v5 import GATE_EFFORT_V5, gate_effort_v5_scorer_for_store
from raidar.scorers.gate_effort_v6 import (
    GATE_EFFORT_V6,
    gate_effort_v6_scorer,
    gate_effort_v6_scorer_for_store,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import (
    PASSED,
    REFUSED,
    _finding,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_gate_effort_v2_scorer import _shadow
from tests.test_gate_effort_v4_scorer import (
    CP1,
    CP2,
    _enforced,
    _graded,
    _report,
    _store_grade,
)

CP3 = "checkpoint_3"


def _assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v6_scorer_for_store(store) if store else gate_effort_v6_scorer()
    return validate_result(GATE_EFFORT_V6, ctx, scorer.assess(ctx))


def _tests_block(*, ran: int, failed: int = 0, coverage: float | None = 90.0) -> dict:
    return {
        "required": True,
        "tests_ran": ran,
        "failed": failed,
        "coverage_percent": coverage,
        "runners": {},
    }


def test_v6_keeps_every_v5_observation_without_repeated_failed_tests(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    steps = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    v5 = validate_result(GATE_EFFORT_V5, ctx, gate_effort_v5_scorer_for_store(store).assess(ctx))
    v6 = _values(_assess(*steps, store=store))

    old = _values(v5)
    assert set(old) <= set(v6)
    assert {key for key in old if old[key] != v6[key]} == set()


def test_a_failed_tests_check_counts_once_and_its_tests_are_a_detail(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=3, failed=1)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    failing = [_finding("TEST.failed") for _ in range(3)]
    _report(_trace(step), 1, [*failing, _finding("C901")], _tests_block(ran=5, failed=3))
    _report(_trace(step), 2, [], _tests_block(ran=5))

    result = _assess(step)

    assert _value(result, "first_run_blocking", CP1) == 2
    assert _value(result, "first_run_blocking.tests", CP1) == 1
    assert _value(result, "blocking_findings", CP1) == 2
    assert _value(result, "first_run_failed_tests", CP1) == 3
    assert _value(result, "first_run_tests_failed", CP1) == 1
    assert _value(result, "first_run_coverage_met", CP1) == 1
    assert _value(result, "step_end_blocking", CP1) == 0
    assert _value(result, "total_first_run_failed_tests", TRAJECTORY_SUBJECT) == 3
    assert _value(result, "first_run_tests_failed_rate", TRAJECTORY_SUBJECT) == 1.0


def test_missed_coverage_is_not_a_test_failure(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=4, failed=0)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(
        _trace(step),
        1,
        [_finding("TEST.coverage-minimum")],
        _tests_block(ran=4, coverage=71.5),
    )
    _report(_trace(step), 2, [], _tests_block(ran=6, coverage=86.0))

    result = _assess(step)

    assert _value(result, "first_run_tests_failed", CP1) == 0
    assert _value(result, "first_run_failed_tests", CP1) == 0
    assert _value(result, "first_run_coverage_met", CP1) == 0
    assert _value(result, "first_run_coverage_met_rate", TRAJECTORY_SUBJECT) == 0.0
    assert _value(result, "first_run_tests_failed_rate", TRAJECTORY_SUBJECT) == 0.0


def test_a_step_where_no_test_ran_has_no_failed_or_coverage_outcome(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=4, failed=0)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(_trace(step), 1, [_finding("TEST.missing")], _tests_block(ran=0, coverage=None))
    _report(_trace(step), 2, [], _tests_block(ran=2))

    observed = _values(_assess(step))

    for metric in ("first_run_tests_failed", "first_run_failed_tests", "first_run_coverage_met"):
        assert observed[(metric, CP1)].status is AssessStatus.NOT_APPLICABLE
    rate = observed[("first_run_tests_failed_rate", TRAJECTORY_SUBJECT)]
    assert rate.status is AssessStatus.NOT_APPLICABLE


def test_a_shadow_gates_new_findings_telescope_to_its_final_tree(tmp_path: Path) -> None:
    steps = [_step(tmp_path, name) for name in (CP1, CP2, CP3)]
    for step, count in zip(steps, (2, 3, 5), strict=True):
        _shadow(_trace(step), REFUSED, findings=[_finding("F841")] * count)

    result = _assess(*steps)

    assert [_value(result, "new_first_run_blocking", s.step) for s in steps] == [2, 1, 2]
    assert _value(result, "total_first_run_blocking", TRAJECTORY_SUBJECT) == 10
    assert _value(result, "total_new_first_run_blocking", TRAJECTORY_SUBJECT) == 5
    assert _value(result, "final_step_end_blocking", TRAJECTORY_SUBJECT) == 5


def test_an_enforced_gates_new_findings_start_from_a_clean_tree(tmp_path: Path) -> None:
    first, second = _enforced(tmp_path)

    result = _assess(first, second)

    assert _value(result, "new_first_run_blocking", CP1) == 2
    assert _value(result, "new_first_run_blocking", CP2) == 0
    assert _value(result, "total_new_first_run_blocking", TRAJECTORY_SUBJECT) == 2
    assert _value(result, "total_first_run_blocking", TRAJECTORY_SUBJECT) == 2
    assert _value(result, "final_step_end_blocking", TRAJECTORY_SUBJECT) == 0


def test_the_untyped_call_cascade_is_reported_beside_the_typing_family(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    findings = [_finding("MYPY.no-untyped-call")] * 4 + [_finding("MYPY.no-untyped-def")]
    _shadow(_trace(step), REFUSED, findings=findings)

    result = _assess(step)

    assert _value(result, "first_run_blocking.typing", CP1) == 5
    assert _value(result, "first_run_untyped_call", CP1) == 4
    assert _value(result, "step_end_untyped_call", CP1) == 4
    assert _value(result, "total_first_run_untyped_call", TRAJECTORY_SUBJECT) == 4
    assert _value(result, "final_step_end_untyped_call", TRAJECTORY_SUBJECT) == 4


def test_counts_from_an_output_whose_codes_are_not_all_listed_are_unavailable(
    tmp_path: Path,
) -> None:
    step = _step(tmp_path, CP1)
    capped = REFUSED.replace("(3 blocking", "(9 blocking")
    _shadow(_trace(step), capped)

    observed = _values(_assess(step))

    for metric in ("first_run_blocking", "first_run_untyped_call", "step_end_blocking"):
        assert observed[(metric, CP1)].status is AssessStatus.UNAVAILABLE


@pytest.mark.parametrize("ref", ["gate-effort@6"])
def test_v6_is_registered_store_bound_with_v5s_metrics_and_more(ref: str) -> None:
    registration = default_scorer_registry().get(ref)
    v5 = {metric.id for metric in GATE_EFFORT_V5.metrics}

    assert registration.store_implementation is not None
    assert v5 < {metric.id for metric in registration.manifest.metrics}
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v6_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@6 takes no config")
