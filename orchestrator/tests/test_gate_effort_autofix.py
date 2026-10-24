"""gate-effort's autofix step, the fix cost by refusal, and the autofix counts across gate modes.

- the autofix passes per step: completed passes, typed failures, files and lines changed,
  block-tier findings fixed by family;
- enforced: the first gate run is the post-autofix count; the raw count adds the first pass's
  removals back; a failed pass makes what depended on it unavailable with its type (the run it
  preceded was raw);
- shadow: the raw counts are the cross-mode ones; the post-autofix ones come from the fixed
  copy's check; fix-side values are not applicable;
- each fix session's cost and wall time split by the kind of the refusal that started it
  (coverage minimum alone apart), adding up to the step's fix totals;
- a gate without autofix gets not-applicable autofix values;
- ``new_autofix_*``: the step's first pass minus what the previous gated step left for autofix;
  enforced, the previous step left 0 (its tree was fixed), so the sum is the first passes';
  shadow, the previous dry run, so the sum telescopes to the last one's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.evaluation.analysis.markdown import (
    CUMULATIVE_LEGEND,
    _autofix_family_table,
    _better,
    _legend,
)
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus
from raidar.scoring.gate_effort.autofix_metrics import REFUSAL_KINDS
from raidar.scoring.gate_effort.families import FAMILIES
from raidar.scoring.gate_effort.refusals import refusal_kind
from tests.gate_effort_support import (
    COVERAGE,
    CP1,
    CP2,
    PASSED,
    REFUSED,
    _assess,
    _at,
    _autofix,
    _enforced,
    _enforced_autofix,
    _pass,
    _post_check,
    _shadow,
    _status,
    _step,
    _trace,
    _value,
    _values,
)
from tests.test_iteration_4_consumers import _arm

T = TRAJECTORY_SUBJECT
LINES, REMOVED = "new_autofix_lines_changed", "new_autofix_removed_blocking"


def _shadow_steps(tmp_path: Path, lines: tuple[int, int], fixed: tuple[dict, dict]) -> tuple:
    steps = (_step(tmp_path, CP1), _step(tmp_path, CP2))
    for step, count, codes in zip(steps, lines, fixed, strict=True):
        trace = _trace(step)
        _shadow(trace, REFUSED, findings=[_at("FORMAT"), _at("C901", line=9)])
        dry = _pass(
            1,
            mode="dry_run",
            lines_changed=count,
            codes_fixed_blocking=codes,
            post_check=_post_check(trace, [_at("C901", line=9)]),
        )
        _autofix(trace, [dry], mode="dry_run")
    return steps


def test_a_gate_without_autofix_has_no_autofix_values_and_raw_counts_are_the_cross_mode_ones(
    tmp_path: Path,
) -> None:
    first, second = _enforced(tmp_path)

    result = _assess(first, second)

    for metric in ("autofix_rounds", "first_pass_clean_post_autofix", "autofix_lines_changed"):
        assert _status(result, metric, CP1) is AssessStatus.NOT_APPLICABLE
    assert _value(result, "first_run_blocking_raw_net", CP1) == _value(
        result, "first_run_blocking_net", CP1
    )
    assert _value(result, "total_new_first_run_blocking_raw_net", T) == _value(
        result, "total_new_first_run_blocking_net", T
    )
    assert _status(result, "first_pass_clean_post_autofix_rate", T) is AssessStatus.NOT_APPLICABLE


# ---- enforced --------------------------------------------------------------------------------


def test_enforced_autofix_counts_and_the_raw_count_adds_the_first_pass_back(
    tmp_path: Path,
) -> None:
    step = _enforced_autofix(tmp_path)

    result = _assess(step)

    assert _value(result, "autofix_rounds", CP1) == 2
    assert _value(result, "autofix_errors", CP1) == 0
    assert _value(result, "autofix_files_changed", CP1) == 1
    assert _value(result, "autofix_lines_changed", CP1) == 6
    assert _value(result, "autofix_removed_blocking", CP1) == 2
    assert _value(result, "autofix_removed_blocking.style", CP1) == 2
    assert _value(result, "first_pass_clean_post_autofix", CP1) == 0
    assert _value(result, "first_run_blocking_post_autofix_net", CP1) == 2
    assert _value(result, "first_run_blocking_raw_net", CP1) == 4
    assert _value(result, "step_end_blocking_post_autofix_net", CP1) == 0
    assert _value(result, "total_new_first_run_blocking_post_autofix_net", T) == 2
    assert _value(result, "total_new_first_run_blocking_raw_net", T) == 4
    assert _value(result, "final_step_end_blocking_post_autofix_net", T) == 0
    assert _value(result, "total_autofix_lines_changed", T) == 6
    assert _value(result, "first_pass_clean_post_autofix_rate", T) == 0


def test_the_fix_cost_is_split_by_the_refusal_that_started_each_session(tmp_path: Path) -> None:
    step = _enforced_autofix(tmp_path)

    result = _assess(step)

    assert _value(result, "fix_cost_usd_by_refusal.mixed", CP1) == pytest.approx(0.05)
    assert _value(result, "fix_wall_s_by_refusal.mixed", CP1) == 120
    others = [kind for kind in REFUSAL_KINDS if kind != "mixed"]
    assert all(_value(result, f"fix_cost_usd_by_refusal.{kind}", CP1) == 0 for kind in others)
    split = sum(_value(result, f"fix_cost_usd_by_refusal.{k}", CP1) or 0 for k in REFUSAL_KINDS)
    assert split == pytest.approx(_value(result, "fix_cost_usd", CP1) or 0)
    assert _value(result, "fix_sessions_with_coverage_minimum", CP1) == 1
    assert _value(result, "fix_cost_usd_with_coverage_minimum", CP1) == pytest.approx(0.05)
    assert _value(result, "total_fix_wall_s_with_coverage_minimum", T) == 120


@pytest.mark.parametrize(
    ("codes", "kind"),
    [
        ((COVERAGE,), "coverage_minimum"),
        ((COVERAGE, "TEST.missing"), "tests"),
        (("FORMAT", "I001"), "style"),
        (("C901", "MYPY.arg-type"), "mixed"),
        ((), "unknown"),
        (None, "unknown"),
    ],
)
def test_refusal_kinds(codes: tuple[str, ...] | None, kind: str) -> None:
    assert refusal_kind(codes) == kind


def test_a_failed_autofix_before_the_first_run_leaves_post_counts_unavailable(
    tmp_path: Path,
) -> None:
    step = _enforced_autofix(tmp_path)
    failed = _pass(
        1,
        before=1,
        error_type="command_failed",
        error="exit 3",
        files_changed=None,
        lines_changed=None,
        codes_fixed=None,
        codes_fixed_blocking=None,
    )
    _autofix(_trace(step), [failed, _pass(2, before=2)])

    result = _assess(step)

    assert (_value(result, "autofix_rounds", CP1), _value(result, "autofix_errors", CP1)) == (1, 1)
    first = _values(result)[("first_run_blocking_post_autofix_net", CP1)]
    assert first.status is AssessStatus.UNAVAILABLE
    assert "command_failed" in (first.reason or "")
    assert _value(result, "first_run_blocking_raw_net", CP1) == 2, "run 1 saw the raw tree"
    assert _value(result, "step_end_blocking_post_autofix_net", CP1) == 0


# ---- shadow ------------------------------------------------------------------------------------


def test_shadow_post_counts_come_from_the_fixed_copy_and_fix_values_are_not_applicable(
    tmp_path: Path,
) -> None:
    first, second = _step(tmp_path, CP1), _step(tmp_path, CP2)
    raw = [_at("FORMAT"), _at("I001", line=2), _at("C901", line=9)]
    for step, post in ((first, [_at("C901", line=9)]), (second, [])):
        trace = _trace(step)
        _shadow(trace, REFUSED, findings=raw)
        _autofix(
            trace, [_pass(1, mode="dry_run", post_check=_post_check(trace, post))], mode="dry_run"
        )

    result = _assess(first, second)

    assert _value(result, "first_run_blocking_raw_net", CP1) == 3
    assert _value(result, "first_run_blocking_post_autofix_net", CP1) == 1
    assert _value(result, "first_pass_clean_post_autofix", CP1) == 0
    assert _value(result, "first_pass_clean_post_autofix", CP2) == 1
    assert _value(result, "step_end_blocking_post_autofix_net", CP2) == 0
    assert [_value(result, "new_first_run_blocking_post_autofix_net", s) for s in (CP1, CP2)] == [
        1,
        -1,
    ]
    assert _value(result, "first_pass_clean_post_autofix_rate", T) == 0.5
    assert _value(result, "autofix_removed_blocking", CP1) == 2
    assert _value(result, "total_autofix_rounds", T) == 2
    for metric in ("fix_cost_usd_by_refusal.mixed", "fix_sessions_with_coverage_minimum"):
        assert _status(result, metric, CP1) is AssessStatus.NOT_APPLICABLE
    assert _status(result, "total_fix_cost_usd_with_coverage_minimum", T) is (
        AssessStatus.NOT_APPLICABLE
    )


def test_a_failed_shadow_pass_is_typed_and_its_post_counts_unavailable(tmp_path: Path) -> None:
    step = _step(tmp_path, CP1)
    trace = _trace(step)
    _shadow(trace, PASSED)
    _autofix(
        trace,
        [_pass(1, mode="dry_run", error_type="copy_failed", error="disk full")],
        mode="dry_run",
    )

    result = _assess(step)

    post = _values(result)[("first_pass_clean_post_autofix", CP1)]
    assert post.status is AssessStatus.UNAVAILABLE
    assert "copy_failed" in (post.reason or "")
    assert _value(result, "autofix_errors", CP1) == 1
    assert _value(result, "first_run_blocking_raw_net", CP1) == 0


def test_enforced_new_counts_are_the_first_pass_since_the_previous_tree_was_fixed(
    tmp_path: Path,
) -> None:
    step = _enforced_autofix(tmp_path)

    result = _assess(step)

    assert _value(result, LINES, CP1) == 6
    assert _value(result, REMOVED, CP1) == 2
    assert _value(result, f"{REMOVED}.style", CP1) == 2
    assert _value(result, f"total_{LINES}", T) == _value(result, "total_autofix_lines_changed", T)
    assert _value(result, f"total_{REMOVED}", T) == 2


def test_shadow_new_counts_telescope_to_the_last_dry_run(tmp_path: Path) -> None:
    fixed = ({"FORMAT": 1, "I001": 1}, {"FORMAT": 1, "I001": 3, "UP017": 1})
    steps = _shadow_steps(tmp_path, (10, 25), fixed)

    result = _assess(*steps)

    assert [_value(result, LINES, step) for step in (CP1, CP2)] == [10, 15]
    assert [_value(result, REMOVED, step) for step in (CP1, CP2)] == [2, 3]
    assert _value(result, "total_autofix_lines_changed", T) == 35, "the cumulative sum"
    assert _value(result, f"total_{LINES}", T) == 25, "the last dry run's count"
    assert _value(result, f"total_{REMOVED}", T) == 5
    assert sum(_value(result, f"total_{REMOVED}.{family}", T) or 0 for family in FAMILIES) == 5


def test_a_failed_shadow_pass_leaves_its_step_and_the_next_unavailable(tmp_path: Path) -> None:
    steps = _shadow_steps(tmp_path, (10, 25), ({"FORMAT": 1}, {"FORMAT": 2}))
    failed = _pass(1, mode="dry_run", error_type="copy_failed", error="disk full")
    _autofix(_trace(steps[0]), [failed], mode="dry_run")

    result = _assess(*steps)

    first = _values(result)[(LINES, CP1)]
    assert first.status is AssessStatus.UNAVAILABLE
    assert "copy_failed" in (first.reason or "")
    after = _values(result)[(LINES, CP2)]
    assert after.status is AssessStatus.UNAVAILABLE
    assert "previous gated step" in (after.reason or "")
    assert _status(result, f"total_{LINES}", T) is AssessStatus.UNAVAILABLE


def test_an_enforced_step_whose_last_pass_failed_leaves_the_next_step_unavailable(
    tmp_path: Path,
) -> None:
    first, second = _enforced(tmp_path)
    good = _pass(1, before=1)
    _autofix(_trace(first), [good, _pass(2, before=2, error_type="timeout", error="120 s")])
    _autofix(_trace(second), [_pass(1, before=1)])

    result = _assess(first, second)

    assert _value(result, LINES, CP1) == 6
    after = _values(result)[(LINES, CP2)]
    assert after.status is AssessStatus.UNAVAILABLE
    assert "timeout" in (after.reason or "")


def test_a_gate_without_autofix_has_no_new_autofix_values(tmp_path: Path) -> None:
    result = _assess(*_enforced(tmp_path))

    assert _status(result, LINES, CP1) is AssessStatus.NOT_APPLICABLE
    assert _status(result, f"total_{REMOVED}", T) is AssessStatus.NOT_APPLICABLE


# ---- consumers --------------------------------------------------------------------------------


def test_the_cumulative_totals_are_marked_and_the_new_ones_are_not() -> None:
    assert _better("gate.total_autofix_lines_changed") == "neutral †"
    assert _better("gate.total_first_run_blocking").endswith(" †")
    assert _better(f"gate.total_{LINES}") == "neutral"
    assert _legend(["gate.total_autofix_rounds"]) == ["", CUMULATIVE_LEGEND, ""]
    assert _legend([f"gate.total_{LINES}"]) == []


def test_the_family_table_shows_the_new_counts_beside_the_sums() -> None:
    arm = _arm(
        "r1",
        {
            "gate.total_autofix_removed_blocking": 40.0,
            "gate.total_autofix_removed_blocking.style": 40.0,
            f"gate.total_{REMOVED}": 9.0,
            f"gate.total_{REMOVED}.style": 9.0,
        },
    )

    table = "\n".join(_autofix_family_table([arm], ["r1"]))

    assert "| style | 40 / 9 |" in table
    assert "each step's own work added" in table
