"""gate-effort@7: first-pass rates that can move and cascade-free counts (smoke-3b-2 O3, O5).

- every @6 observation is kept, value for value;
- the three first-pass variants ignore auto-fixable layout codes, the tests
  check, or both, are decided on the same steps as ``first_pass_clean`` (shadow
  gates included) and are not applicable on a step with no source change;
- the cross-mode blocking counts are reported without the
  ``MYPY.no-untyped-call`` cascade; ``new_*`` telescopes as @6's does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort_v6 import GATE_EFFORT_V6, gate_effort_v6_scorer_for_store
from raidar.scorers.gate_effort_v7 import (
    GATE_EFFORT_V7,
    gate_effort_v7_scorer,
    gate_effort_v7_scorer_for_store,
)
from raidar.scorers.gate_effort_v7.scorer import AUTOFIX_CODES
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
from tests.test_gate_effort_v4_scorer import CP1, CP2, _enforced, _graded, _report, _store_grade

CP3 = "checkpoint_3"
VARIANTS = (
    "first_pass_clean_except_autofix",
    "first_pass_clean_except_tests",
    "first_pass_clean_except_autofix_tests",
)


def _assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v7_scorer_for_store(store) if store else gate_effort_v7_scorer()
    return validate_result(GATE_EFFORT_V7, ctx, scorer.assess(ctx))


def test_v7_keeps_every_v6_observation(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    steps = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    v6 = _values(
        validate_result(GATE_EFFORT_V6, ctx, gate_effort_v6_scorer_for_store(store).assess(ctx))
    )
    v7 = _values(_assess(*steps, store=store))

    assert set(v6) < set(v7)
    assert {key for key in v6 if v6[key] != v7[key]} == set()


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        (["FORMAT", "I001"], (1, 0, 1)),
        (["FORMAT", "TEST.coverage-minimum"], (0, 0, 1)),
        (["TEST.missing", "TEST.failed", "TEST.failed"], (0, 1, 1)),
        (["FORMAT", "C901", "TEST.coverage-minimum"], (0, 0, 0)),
        (["W291", "W293", "W292"], (1, 0, 1)),
        (["F401"], (0, 0, 0)),
    ],
)
def test_each_first_pass_variant_ignores_only_its_codes(
    tmp_path: Path, codes: list[str], expected: tuple[int, int, int]
) -> None:
    step = _step(tmp_path, CP1)
    _shadow(_trace(step), REFUSED, findings=[_finding(code) for code in codes])

    result = _assess(step)

    assert _value(result, "first_pass_clean", CP1) == 0
    assert tuple(_value(result, name, CP1) for name in VARIANTS) == expected
    for name, value in zip(VARIANTS, expected, strict=True):
        assert _value(result, f"{name}_rate", TRAJECTORY_SUBJECT) == value


def test_a_clean_first_run_is_clean_in_every_variant(tmp_path: Path) -> None:
    first, second = _step(tmp_path, CP1), _step(tmp_path, CP2)
    _shadow(_trace(first), PASSED, findings=[])
    _shadow(_trace(second), REFUSED, findings=[_finding("FORMAT"), _finding("C901")])

    result = _assess(first, second)

    assert [_value(result, name, CP1) for name in VARIANTS] == [1, 1, 1]
    assert _value(result, "first_pass_clean_except_autofix_rate", TRAJECTORY_SUBJECT) == 0.5
    assert _value(result, "first_pass_clean_rate", TRAJECTORY_SUBJECT) == 0.5


def test_the_autofix_set_is_layout_and_import_order_only() -> None:
    assert {"FORMAT", "I001", "W291", "W292", "W293"} == AUTOFIX_CODES


def test_the_cascade_is_left_out_of_the_cross_mode_counts(tmp_path: Path) -> None:
    steps = [_step(tmp_path, name) for name in (CP1, CP2, CP3)]
    cascades = (3, 6, 10)
    for step, cascade, other in zip(steps, cascades, (1, 2, 2), strict=True):
        findings = [_finding("MYPY.no-untyped-call")] * cascade + [_finding("C901")] * other
        _shadow(_trace(step), REFUSED, findings=findings)

    result = _assess(*steps)

    assert [_value(result, "first_run_blocking", s.step) for s in steps] == [4, 8, 12]
    assert [_value(result, "first_run_blocking_ex_cascade", s.step) for s in steps] == [1, 2, 2]
    assert [_value(result, "step_end_blocking_ex_cascade", s.step) for s in steps] == [1, 2, 2]
    assert [_value(result, "new_first_run_blocking_ex_cascade", s.step) for s in steps] == [
        1,
        1,
        0,
    ]
    assert _value(result, "total_new_first_run_blocking_ex_cascade", TRAJECTORY_SUBJECT) == 2
    assert _value(result, "final_step_end_blocking_ex_cascade", TRAJECTORY_SUBJECT) == 2
    assert _value(result, "total_new_first_run_blocking", TRAJECTORY_SUBJECT) == 12
    assert _value(result, "final_step_end_blocking", TRAJECTORY_SUBJECT) == 12


def test_an_enforced_gates_counts_without_the_cascade_start_clean(tmp_path: Path) -> None:
    first, second = _enforced(tmp_path)

    result = _assess(first, second)

    assert _value(result, "new_first_run_blocking_ex_cascade", CP1) == _value(
        result, "new_first_run_blocking", CP1
    )
    assert _value(result, "step_end_blocking_ex_cascade", CP1) == 0
    assert _value(result, "final_step_end_blocking_ex_cascade", TRAJECTORY_SUBJECT) == 0


def test_unlisted_codes_leave_the_variants_and_counts_unavailable(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _shadow(_trace(step), REFUSED.replace("(3 blocking", "(9 blocking"))

    observed = _values(_assess(step))

    for metric in (*VARIANTS, "first_run_blocking_ex_cascade", "step_end_blocking_ex_cascade"):
        assert observed[(metric, CP1)].status is AssessStatus.UNAVAILABLE, metric


def test_a_step_with_a_fix_round_reads_its_first_run(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=4, failed=0)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(_trace(step), 1, [_finding("FORMAT"), _finding("TEST.coverage-minimum")], None)
    _report(_trace(step), 2, [], None)

    result = _assess(step)

    assert [_value(result, name, CP1) for name in VARIANTS] == [0, 0, 1]


def test_v7_is_registered_store_bound_and_takes_no_config() -> None:
    registration = default_scorer_registry().get("gate-effort@7")
    v6 = {metric.id for metric in GATE_EFFORT_V6.metrics}

    assert registration.store_implementation is not None
    assert v6 < {metric.id for metric in registration.manifest.metrics}
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v7_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@7 takes no config")
