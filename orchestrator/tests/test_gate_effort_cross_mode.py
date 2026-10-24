"""gate-effort's blocking counts and first-pass variants that compare across gate modes.

- a failed tests check counts once in every blocking count; the failing tests are their own
  detail (``first_run_failed_tests``); failing tests and a missed coverage minimum are separate
  first-run outcomes;
- ``new_first_run_blocking`` telescopes a shadow gate's re-counted debt to the final tree, and
  ``final_step_end_blocking`` is that tree in both modes;
- the mypy ``no-untyped-call`` cascade is reported beside the typing family and left out of the
  ``*_ex_cascade`` counts;
- the three first-pass variants ignore auto-fixable layout codes, the tests check, or both;
- ``*_net`` is ``*_ex_cascade`` with each ``MYPY.unused-ignore`` at a line that also has a
  ``SUPPRESS`` folded into it (located from the report, else the complete output log); a folded
  code whose location the stored files do not give in full leaves the net count unavailable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus
from raidar.scoring.gate_effort.cross_mode.metrics import AUTOFIX_CODES
from raidar.scoring.gate_effort.folding import FOLDED_CODES, SUPPRESS_CODE
from tests.gate_effort_support import (
    CP1,
    CP2,
    CP3,
    PASSED,
    REFUSED,
    _assess,
    _at,
    _enforced,
    _finding,
    _graded,
    _report,
    _shadow,
    _step,
    _tests_block,
    _trace,
    _value,
    _values,
    _write_gate,
)

UNUSED_IGNORE = "MYPY.unused-ignore"
VARIANTS = (
    "first_pass_clean_except_autofix",
    "first_pass_clean_except_tests",
    "first_pass_clean_except_autofix_tests",
)


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


def test_an_unused_ignore_at_a_suppress_line_is_folded(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    findings = [
        _at(SUPPRESS_CODE, line=359),
        _at(UNUSED_IGNORE, line=359),
        _at(UNUSED_IGNORE, line=12),  # another line: a real finding, kept
        _at("PYS250", line=333),
        _at(UNUSED_IGNORE, "other.py", 359),  # same line, other file: kept
        _at(UNUSED_IGNORE, line=359, tier="warn"),  # not blocking: never counted
    ]
    _shadow(_trace(step), REFUSED, findings=findings)

    result = _assess(step)

    assert _value(result, "first_run_blocking_ex_cascade", CP1) == 5
    assert _value(result, "first_run_blocking_net", CP1) == 4
    assert _value(result, "step_end_blocking_net", CP1) == 4
    assert _value(result, "total_new_first_run_blocking_net", TRAJECTORY_SUBJECT) == 4
    assert _value(result, "final_step_end_blocking_net", TRAJECTORY_SUBJECT) == 4


def test_net_counts_leave_out_the_cascade_and_telescope(tmp_path: Path) -> None:
    first, second = _step(tmp_path, CP1), _step(tmp_path, CP2)
    _shadow(
        _trace(first),
        REFUSED,
        findings=[_at("MYPY.no-untyped-call"), _at("C901"), _at(SUPPRESS_CODE, line=7)],
    )
    _shadow(
        _trace(second),
        REFUSED,
        findings=[
            _at("MYPY.no-untyped-call"),
            _at("C901"),
            _at(SUPPRESS_CODE, line=7),
            _at(UNUSED_IGNORE, line=7),
            _at("F401", line=2),
        ],
    )

    result = _assess(first, second)

    assert [_value(result, "first_run_blocking_net", s) for s in (CP1, CP2)] == [2, 3]
    assert [_value(result, "new_first_run_blocking_net", s) for s in (CP1, CP2)] == [2, 1]
    assert _value(result, "total_new_first_run_blocking_net", TRAJECTORY_SUBJECT) == 3
    assert _value(result, "final_step_end_blocking_net", TRAJECTORY_SUBJECT) == 3
    assert _value(result, "final_step_end_blocking_ex_cascade", TRAJECTORY_SUBJECT) == 4


def test_an_enforced_step_reads_its_first_and_last_decided_runs(tmp_path: Path) -> None:
    step = _graded(_step(tmp_path, CP1), passed=4, failed=0)
    _write_gate(_trace(step), outputs=[REFUSED, PASSED], termination="gate_clean", signal=True)
    _report(
        _trace(step),
        1,
        [_at(SUPPRESS_CODE, line=4), _at(UNUSED_IGNORE, line=4), _at("FORMAT")],
        None,
    )
    _report(_trace(step), 2, [], None)

    result = _assess(step)

    assert _value(result, "first_run_blocking_ex_cascade", CP1) == 3
    assert _value(result, "first_run_blocking_net", CP1) == 2
    assert _value(result, "step_end_blocking_net", CP1) == 0


def test_the_complete_output_log_locates_the_pair_without_a_report(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    output = (
        "guardrail-check: REFUSED (3 blocking, 0 warnings, 1 Python files)\n"
        "Blocking (fix these; the change is refused until they pass) (3):\n"
        "  forge.py:9:22: SUPPRESS lint/type suppression comments are not allowed\n"
        '  forge.py:9:1: MYPY.unused-ignore Unused "type: ignore" comment\n'
        "  forge.py:3:1: C901 `run` is too complex (11 > 10)\n"
    )
    _shadow(_trace(step), output)

    result = _assess(step)

    assert _value(result, "first_run_blocking_ex_cascade", CP1) == 3
    assert _value(result, "first_run_blocking_net", CP1) == 2


def test_a_run_without_the_pair_needs_no_location(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    _shadow(_trace(step), REFUSED)  # listed codes only, no report

    result = _assess(step)

    assert _value(result, "first_run_blocking_net", CP1) == _value(
        result, "first_run_blocking_ex_cascade", CP1
    )


def test_an_unlocatable_pair_leaves_the_net_count_unavailable(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    capped = (
        "guardrail-check: REFUSED (4 blocking, 0 warnings, 1 Python files)\n"
        "Blocking (fix these; the change is refused until they pass) (4):\n"
        "  forge.py:9:22: SUPPRESS lint/type suppression comments are not allowed\n"
        '  forge.py:9:1: MYPY.unused-ignore Unused "type: ignore" comment\n'
        "  ... 2 more (see the JSON report)\n"
    )
    _shadow(_trace(step), capped)

    observed = _values(_assess(step))

    for metric in ("first_run_blocking_net", "step_end_blocking_net"):
        assert observed[(metric, CP1)].status is AssessStatus.UNAVAILABLE, metric


def test_the_folded_codes_are_mypys_unused_ignore_only() -> None:
    assert frozenset({UNUSED_IGNORE}) == FOLDED_CODES
