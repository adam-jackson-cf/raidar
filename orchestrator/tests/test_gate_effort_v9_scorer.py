"""gate-effort@9: guardrail-gate@4's autofix step and the fix cost by refusal (iteration 4).

- every @8 observation is kept, value for value;
- the autofix passes per step: completed passes, typed failures, files and lines
  changed, block-tier findings fixed by family;
- enforced: the first gate run is the post-autofix count; the raw count adds the
  first pass's removals back; a failed pass makes what depended on it
  unavailable with its type (the run it preceded was raw);
- shadow: the raw counts are @8's; the post-autofix ones come from the fixed
  copy's check; fix-side values are not applicable;
- each fix session's cost and wall time split by the kind of the refusal that
  started it (coverage minimum alone apart), adding up to @1's totals;
- a gate without autofix (guardrail-gate@3) gets not-applicable autofix values
  and its raw counts are @8's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort.records import RECORD_PATH
from raidar.scorers.gate_effort_v8 import GATE_EFFORT_V8, gate_effort_v8_scorer_for_store
from raidar.scorers.gate_effort_v9 import (
    GATE_EFFORT_V9,
    gate_effort_v9_scorer,
    gate_effort_v9_scorer_for_store,
)
from raidar.scorers.gate_effort_v9.manifest import REFUSAL_KINDS
from raidar.scorers.gate_effort_v9.refusals import refusal_kind
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import (
    PASSED,
    REFUSED,
    _stage,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_gate_effort_v2_scorer import _shadow
from tests.test_gate_effort_v4_scorer import CP1, CP2, _enforced, _report, _store_grade
from tests.test_gate_effort_v8_scorer import _at

T = TRAJECTORY_SUBJECT
COVERAGE = "TEST.coverage-minimum"


def _assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v9_scorer_for_store(store) if store else gate_effort_v9_scorer()
    return validate_result(GATE_EFFORT_V9, ctx, scorer.assess(ctx))


def _status(result: AssessResult, metric: str, subject: str) -> AssessStatus:
    return _values(result)[(metric, subject)].status


def _pass(run: int, *, mode: str = "apply", before: int | None = None, **extra: Any) -> dict:
    return {
        "run": run,
        "mode": mode,
        "before_gate_run": before if mode == "apply" else None,
        "after_gate_run": 1 if mode == "dry_run" else None,
        "error_type": None,
        "error": None,
        "files_changed": ["forge.py"],
        "lines_changed": 6,
        "codes_fixed": {"FORMAT": 1, "I001": 1, "D202": 1},
        "codes_fixed_blocking": {"FORMAT": 1, "I001": 1},
        "post_check": None,
        **extra,
    }


def _autofix(trace: Path, runs: list[dict], *, mode: str = "apply") -> None:
    record = json.loads((trace / RECORD_PATH).read_text())
    record["autofix"] = {
        "enabled": True,
        "command": "guardrail-check --autofix",
        "mode": mode,
        "runs": runs,
        "errors": sum(1 for run in runs if run["error_type"]),
    }
    (trace / RECORD_PATH).write_text(json.dumps(record))


def _post_check(trace: Path, findings: list[dict]) -> dict:
    folder = trace / "guardrail-gate" / "post_autofix_run_1"
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "python-guardrails.gate-report/1",
        "verdict": "refused" if findings else "passed",
        "findings": findings,
    }
    (folder / "gate-1.json").write_text(json.dumps(report))
    return {
        "run": 1,
        "after": "stage_1_work",
        "passed": not findings,
        "error": None,
        "timed_out": False,
        "log": "guardrail-gate/post_autofix_run_1.log",
        "report_dir": "guardrail-gate/post_autofix_run_1",
        "mode": "shadow",
    }


# ---- @8 kept ---------------------------------------------------------------------------------


def test_v9_keeps_every_v8_observation(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    steps = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    v8 = _values(
        validate_result(GATE_EFFORT_V8, ctx, gate_effort_v8_scorer_for_store(store).assess(ctx))
    )
    v9 = _values(_assess(*steps, store=store))

    assert set(v8) < set(v9)
    assert {key for key in v8 if v8[key] != v9[key]} == set()


def test_registration_and_config() -> None:
    registration = default_scorer_registry().get("gate-effort@9")
    assert registration.manifest.version == "9"
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v9_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@9 takes no config")


# ---- a gate without autofix ------------------------------------------------------------------


def test_a_gate_without_autofix_has_no_autofix_values_and_raw_counts_are_v8s(
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


def _enforced_autofix(tmp_path: Path) -> StepInputs:
    """cp1: autofix, then refused for C901 and the coverage minimum, fixed in one round."""
    step = _step(tmp_path, CP1)
    trace = _trace(step)
    _write_gate(
        trace,
        outputs=[REFUSED, PASSED],
        termination="gate_clean",
        signal=True,
        stages=[_stage(1, "work", 0), _stage(2, "fix", 1, cost=0.05, seconds=120.0)],
    )
    _report(trace, 1, [_at("C901"), _at(COVERAGE)], None)
    _report(trace, 2, [], None)
    _autofix(
        trace,
        [
            _pass(1, before=1),
            _pass(
                2,
                before=2,
                files_changed=[],
                lines_changed=0,
                codes_fixed={},
                codes_fixed_blocking={},
            ),
        ],
    )
    return step


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
