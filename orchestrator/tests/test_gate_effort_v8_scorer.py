"""gate-effort@8: a refused suppression comment counted once (smoke-3b-3 I4).

- every @7 observation is kept, value for value;
- ``*_net`` is @7's ``*_ex_cascade`` with each ``MYPY.unused-ignore`` at a line
  that also has a ``SUPPRESS`` folded into it (located from the report, else the
  complete output log); ``new_*_net`` telescopes as @6's and @7's counts do;
- a folded code whose location the stored files do not give in full leaves the
  net count unavailable, never a guess.
"""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort_v7 import GATE_EFFORT_V7, gate_effort_v7_scorer_for_store
from raidar.scorers.gate_effort_v8 import (
    GATE_EFFORT_V8,
    gate_effort_v8_scorer,
    gate_effort_v8_scorer_for_store,
)
from raidar.scorers.gate_effort_v8.folding import FOLDED_CODES, SUPPRESS_CODE
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import (
    PASSED,
    REFUSED,
    _step,
    _trace,
    _value,
    _values,
    _write_gate,
)
from tests.test_gate_effort_v2_scorer import _shadow
from tests.test_gate_effort_v4_scorer import CP1, CP2, _enforced, _graded, _report, _store_grade

UNUSED_IGNORE = "MYPY.unused-ignore"


def _at(code: str, relpath: str = "forge.py", line: int = 1, tier: str = "block") -> dict:
    return {"code": code, "relpath": relpath, "line": line, "message": "m", "tier": tier}


def _assess(*steps: StepInputs, store: Path | None = None) -> AssessResult:
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    scorer = gate_effort_v8_scorer_for_store(store) if store else gate_effort_v8_scorer()
    return validate_result(GATE_EFFORT_V8, ctx, scorer.assess(ctx))


def test_v8_keeps_every_v7_observation(tmp_path: Path) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    steps = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    v7 = _values(
        validate_result(GATE_EFFORT_V7, ctx, gate_effort_v7_scorer_for_store(store).assess(ctx))
    )
    v8 = _values(_assess(*steps, store=store))

    assert set(v7) < set(v8)
    assert {key for key in v7 if v7[key] != v8[key]} == set()


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


def test_v8_is_registered_store_bound_and_takes_no_config() -> None:
    registration = default_scorer_registry().get("gate-effort@8")
    v7 = {metric.id for metric in GATE_EFFORT_V7.metrics}

    assert registration.store_implementation is not None
    assert v7 < {metric.id for metric in registration.manifest.metrics}
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v8_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@8 takes no config")
