"""``gate-effort@6``: gate-effort@5 with blocking counts that compare across gate modes.

``gate-effort@5`` (``raidar.scorers.gate_effort_v5``) is released and unchanged.
``smoke-04`` of the guardrails experiment showed three measurement defects in
it; @6 keeps every @5 metric, status, reason and evidence item except where
this list says otherwise:

- **A failed tests check counts once.** The gate reports one ``TEST.failed``
  finding per failing test (capped at 20), while a missed coverage minimum or
  missing tests count once. In @6 every blocking count (``first_run_blocking``,
  ``blocking_findings``, their families and totals) counts a run's
  ``TEST.failed`` findings as one finding; the number of failing tests is kept
  as its own detail, ``first_run_failed_tests``. A run whose stored files do
  not list every blocking code cannot be counted that way: its counts are
  ``unavailable`` (@5 kept the total and dropped only the families).
- **Blocking counts that mean the same thing in both gate modes.** A shadow
  gate checks the whole tree at every step and never fixes anything, so its
  per-step first-run counts repeat the accumulated debt, and their trajectory
  sum (``total_first_run_blocking``) counts it again at every step; a commit
  gate's first run starts from a tree its previous step left clean, so its sum
  is new findings only. @6 adds, for both modes:

  - ``step_end_blocking``: blocking findings of the step's last decided gate
    run (a shadow gate's only run), i.e. the tree the step handed on;
  - ``new_first_run_blocking``: the step's first-run blocking count minus the
    previous gated step's ``step_end_blocking`` (0 before the first gated
    step): what the step's own work added before any fix (signed: a step that
    removes debt is negative). ``total_new_first_run_blocking`` is its sum (for
    a shadow gate it telescopes to the final tree's count);
  - ``final_step_end_blocking``: ``step_end_blocking`` of the last gated step.
- **The typing cascade is counted separately.** One untyped ``def`` makes mypy
  report ``no-untyped-call`` at every call site, so the ``typing`` family
  counts call sites rather than defects. The family map is unchanged (the
  cascade stays in ``typing``); @6 reports the cascade beside it:
  ``first_run_untyped_call`` and ``step_end_untyped_call`` per step,
  ``total_first_run_untyped_call`` and ``final_step_end_untyped_call``.
- **Failing tests and missed coverage are separate outcomes.** @5's
  ``first_run_tests_passed`` fails on either. @6 adds
  ``first_run_tests_failed`` (the first run's tests failed or the run timed
  out; ``not_applicable`` when no test ran), ``first_run_coverage_met`` (the
  first run's owned coverage met the minimum; ``not_applicable`` when no
  coverage was measured) and ``first_run_failed_tests`` (failing tests in the
  first run), with ``first_run_tests_failed_rate``,
  ``first_run_coverage_met_rate`` and ``total_first_run_failed_tests``. On a
  step with no source change they are ``not_applicable``, as @5's first-run
  tests outcomes are.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from raidar.scorers.gate_effort.records import GateRunView, StepGate
from raidar.scorers.gate_effort.scorer import (
    Measured,
    _all,
    _first_pass,
    _incomplete,
    _read,
    _Step,
    outcome_values,
    trajectory_values,
    usage_values,
)
from raidar.scorers.gate_effort_v2.scorer import (
    NO_GATE,
    SHADOW_STEP_NA,
    SHADOW_TOTAL_NA,
    _modes,
    _shadowed,
)
from raidar.scorers.gate_effort_v3.scorer import FIRST_PASS_CLEAN, NO_SOURCE_CHANGE, unchanged_steps
from raidar.scorers.gate_effort_v4.scorer import (
    BLOCKING_FINDINGS,
    FAMILIES,
    FIRST_RUN_BLOCKING,
    NO_EDIT_NA,
    NO_TESTS_BLOCK,
    SHADOW_NEW_NA,
    _family_counts,
    _Grades,
    _metric,
    _record,
    _run_tests,
    _RunTests,
    correctness_values,
    new_trajectory_values,
    tests_values,
)
from raidar.scorers.gate_effort_v5.scorer import (
    GATE_EFFORT_V5,
    GateEffortV5Scorer,
    evidence,
)
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import Direction, MetricLevel, MetricType, ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_HIGHER, _LOWER = Direction.HIGHER_BETTER, Direction.LOWER_BETTER
FAILED_CODE = "TEST.failed"
COVERAGE_CODE = "TEST.coverage-minimum"
UNTYPED_CALL_CODE = "MYPY.no-untyped-call"
STEP_END_BLOCKING = "step_end_blocking"
NEW_FIRST_RUN_BLOCKING = "new_first_run_blocking"
FIRST_RUN_UNTYPED_CALL = "first_run_untyped_call"
STEP_END_UNTYPED_CALL = "step_end_untyped_call"
FIRST_RUN_TESTS_FAILED = "first_run_tests_failed"
FIRST_RUN_COVERAGE_MET = "first_run_coverage_met"
FIRST_RUN_FAILED_TESTS = "first_run_failed_tests"
NO_TEST_RAN = "no test ran in the first gate run"
NO_COVERAGE = "the first gate run measured no coverage (no test ran)"
CODES_UNLISTED = (
    "the run's blocking codes are not all listed, so a failed tests check cannot be counted once"
)

V6_STEP_METRICS = (
    _metric(
        STEP_END_BLOCKING,
        "Blocking findings of the step's last decided gate run (a failed tests check once).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        NEW_FIRST_RUN_BLOCKING,
        "First-run blocking findings minus the previous gated step's step-end count.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        FIRST_RUN_UNTYPED_CALL,
        "First-run MYPY.no-untyped-call findings (the call-site cascade of untyped defs).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        STEP_END_UNTYPED_CALL,
        "MYPY.no-untyped-call findings of the step's last decided gate run.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        FIRST_RUN_TESTS_FAILED,
        "The first gate run's own tests failed or timed out (not applicable: no test ran).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    _metric(
        FIRST_RUN_COVERAGE_MET,
        "The first gate run's owned line coverage met the minimum (when measured).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        FIRST_RUN_FAILED_TESTS,
        "Failing tests in the first gate run's tests check.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
)
V6_TRAJECTORY_METRICS = (
    _metric(
        f"total_{NEW_FIRST_RUN_BLOCKING}",
        "Blocking findings the steps' own work added before any fix, over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        f"final_{STEP_END_BLOCKING}",
        "Blocking findings at the end of the last gated step.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        f"total_{FIRST_RUN_UNTYPED_CALL}",
        "First-run MYPY.no-untyped-call findings over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        f"final_{STEP_END_UNTYPED_CALL}",
        "MYPY.no-untyped-call findings at the end of the last gated step.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        f"{FIRST_RUN_TESTS_FAILED}_rate",
        "Share of steps whose first gate run ran tests in which they failed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    _metric(
        f"{FIRST_RUN_COVERAGE_MET}_rate",
        "Share of steps whose first gate run measured coverage in which it met the minimum.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        f"total_{FIRST_RUN_FAILED_TESTS}",
        "Failing tests in the first gate runs over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)
GATE_EFFORT_V6 = GATE_EFFORT_V5.model_copy(
    update={
        "version": "6",
        "description": (
            "Effort a guardrail-gate commit gate cost per step, the gate's tests check and "
            "the fix rounds' correctness (gate-effort@5), with a failed tests check counted "
            "once in every blocking count, blocking counts that compare across gate modes "
            "(step-end, new per step, final), the mypy no-untyped-call cascade reported "
            "separately, and failing tests and missed coverage as separate first-run "
            "outcomes. A shadow gate reports its first-run, step-end and code outcomes "
            "only. Not applicable to an arm without a gate."
        ),
        "metrics": (*GATE_EFFORT_V5.metrics, *V6_STEP_METRICS, *V6_TRAJECTORY_METRICS),
    }
)
V6_NO_EDIT_NA = (
    *NO_EDIT_NA,
    FIRST_RUN_TESTS_FAILED,
    FIRST_RUN_COVERAGE_MET,
    FIRST_RUN_FAILED_TESTS,
)
_V6_STEP = tuple(metric.id for metric in GATE_EFFORT_V6.metrics if metric.level is _CHECKPOINT)


# ---- blocking counts with a failed tests check once -----------------------------------


def codes_once(run: GateRunView) -> tuple[str, ...] | None:
    """The run's blocking codes with its ``TEST.failed`` findings as one (None: unlisted)."""
    if run.findings.codes is None:
        return () if run.findings.total == 0 else None
    seen_failed = False
    kept: list[str] = []
    for code in run.findings.codes:
        if code == FAILED_CODE:
            if seen_failed:
                continue
            seen_failed = True
        kept.append(code)
    return tuple(kept)


def _listed_codes(runs: Sequence[GateRunView]) -> list[str] | str:
    """Every run's blocking codes, a failed tests check once per run; or why they are unknown."""
    unknown = next((run for run in runs if run.findings.total is None), None)
    if unknown is not None:
        return _incomplete(unknown)
    listed = [codes_once(run) for run in runs]
    if any(codes is None for codes in listed):
        return CODES_UNLISTED
    return [code for found in listed for code in found or ()]


def _counted(prefix: str, runs: Sequence[GateRunView]) -> dict[str, Measured]:
    """Total and families over ``runs``, a failed tests check counted once per run."""
    names = [prefix, *(f"{prefix}.{family}" for family in FAMILIES)]
    codes = _listed_codes(runs)
    if isinstance(codes, str):
        return _all(names, Measured.gap(_UNAVAILABLE, codes))
    counts = _family_counts(codes)
    return {prefix: Measured(float(len(codes)))} | {
        f"{prefix}.{family}": Measured(float(counts[family])) for family in FAMILIES
    }


def _untyped_calls(run: GateRunView) -> Measured:
    if run.findings.total == 0:
        return Measured(0.0)
    if run.findings.codes is None:
        return Measured.gap(_UNAVAILABLE, _incomplete(run))
    return Measured(float(sum(code == UNTYPED_CALL_CODE for code in run.findings.codes)))


def finding_values(gate: StepGate) -> dict[str, Measured]:
    """@5's finding values with a failed tests check counted once, plus the step end."""
    first_names = [FIRST_RUN_BLOCKING, *(f"{FIRST_RUN_BLOCKING}.{f}" for f in FAMILIES)]
    first = _first_pass(gate)
    if first.status is not _OK:
        gap = Measured.gap(first.status, first.reason or "")
        values = _all([*first_names, FIRST_RUN_UNTYPED_CALL], gap)
    else:
        values = _counted(FIRST_RUN_BLOCKING, gate.runs[:1])
        values[FIRST_RUN_UNTYPED_CALL] = _untyped_calls(gate.runs[0])
    decided = [run for run in gate.runs if run.decided]
    if not decided:
        names = [BLOCKING_FINDINGS, *(f"{BLOCKING_FINDINGS}.{f}" for f in FAMILIES)]
        gap = Measured.gap(_INSUFFICIENT, first.reason or "no run decided")
        return values | _all([*names, STEP_END_BLOCKING, STEP_END_UNTYPED_CALL], gap)
    last = decided[-1]
    return (
        values
        | _counted(BLOCKING_FINDINGS, decided)
        | {STEP_END_BLOCKING: _counted(STEP_END_BLOCKING, [last])[STEP_END_BLOCKING]}
        | {STEP_END_UNTYPED_CALL: _untyped_calls(last)}
    )


# ---- failing tests and coverage, separately ---------------------------------------------


def _failed_tests(block: Mapping[str, Any], codes: Sequence[str]) -> dict[str, Measured]:
    """Whether the first run's own tests failed, and how many (not applicable: none ran)."""
    failed = FAILED_CODE in codes
    if not (int(block.get("tests_ran") or 0) or failed):
        gap = Measured.gap(_NA, NO_TEST_RAN)
        return {FIRST_RUN_TESTS_FAILED: gap, FIRST_RUN_FAILED_TESTS: gap}
    return {
        FIRST_RUN_TESTS_FAILED: Measured(1.0 if failed else 0.0),
        FIRST_RUN_FAILED_TESTS: Measured(float(int(block.get("failed") or 0))),
    }


def _coverage_met(block: Mapping[str, Any], codes: Sequence[str]) -> Measured:
    if not isinstance(block.get("coverage_percent"), int | float):
        return Measured.gap(_NA, NO_COVERAGE)
    return Measured(0.0 if COVERAGE_CODE in codes else 1.0)


def _first_tests(run: _RunTests | None) -> dict[str, Measured]:
    """The first run's tests failed / coverage met / failing tests, or why not measured."""
    names = (FIRST_RUN_TESTS_FAILED, FIRST_RUN_COVERAGE_MET, FIRST_RUN_FAILED_TESTS)
    if run is None or not run.decided:
        return _all(names, Measured.gap(_INSUFFICIENT, "no decided gate run for the first run"))
    if run.block is None:
        return _all(names, Measured.gap(_NA, NO_TESTS_BLOCK))
    codes = run.block.get("blocking_codes") or []
    return _failed_tests(run.block, codes) | {
        FIRST_RUN_COVERAGE_MET: _coverage_met(run.block, codes)
    }


def v6_tests_values(trace_dir: Path | None, record: Mapping[str, Any]) -> dict[str, Measured]:
    runs = _run_tests(trace_dir, record)
    return tests_values(trace_dir, record) | _first_tests(runs[0] if runs else None)


# ---- one step and the trajectory -------------------------------------------------------


def _step_values(step: _Step, grades: _Grades) -> dict[str, Measured]:
    if not step.inputs.reached:
        return _all(_V6_STEP, Measured.gap(_NA, "checkpoint not reached"))
    if step.error is not None:
        return _all(_V6_STEP, Measured.gap(_UNAVAILABLE, step.error))
    if step.gate is None:
        reason = "no guardrail-gate record for this step, though other steps have one"
        return _all(_V6_STEP, Measured.gap(_UNAVAILABLE, reason))
    record = _record(step.inputs)
    values = (
        outcome_values(step.gate)
        | finding_values(step.gate)
        | usage_values(step.gate)
        | v6_tests_values(step.inputs.trace_dir, record)
        | correctness_values(step.inputs, record, grades)
    )
    # Filled in over the trajectory (it needs the previous gated step's end).
    values[NEW_FIRST_RUN_BLOCKING] = Measured.gap(_UNAVAILABLE, "not computed")
    return values


def with_new_findings(per_step: Sequence[tuple[_Step, dict[str, Measured]]]) -> None:
    """Set each gated step's ``new_first_run_blocking`` from the previous gated step's end."""
    previous = Measured(0.0)
    for step, values in per_step:
        if not step.inputs.reached:
            values[NEW_FIRST_RUN_BLOCKING] = Measured.gap(_NA, "checkpoint not reached")
            continue
        if not step.gated:
            # A reached step without a gate record changed the tree unseen.
            previous = Measured.gap(_UNAVAILABLE, f"{step.inputs.step} has no gate record")
            continue
        first = values[FIRST_RUN_BLOCKING]
        if first.status is not _OK:
            values[NEW_FIRST_RUN_BLOCKING] = first
        elif previous.status is not _OK or previous.value is None:
            reason = f"the previous gated step's end count is unknown: {previous.reason}"
            values[NEW_FIRST_RUN_BLOCKING] = Measured.gap(_UNAVAILABLE, reason)
        else:
            values[NEW_FIRST_RUN_BLOCKING] = Measured((first.value or 0.0) - previous.value)
        previous = values[STEP_END_BLOCKING]


def _adjusted(
    step: _Step, values: dict[str, Measured], shadow: bool, unchanged: frozenset[str]
) -> dict[str, Measured]:
    """@4's shadow and no-edit rules, extended to @6's first-run tests outcomes."""
    if shadow:
        values = _shadowed(values, (*SHADOW_STEP_NA, *SHADOW_NEW_NA))
    if step.gate is not None and step.inputs.step in unchanged:
        gap = Measured.gap(_NA, NO_SOURCE_CHANGE)
        values = values | {
            name: gap for name in (FIRST_PASS_CLEAN, *V6_NO_EDIT_NA) if values[name].status is _OK
        }
    return values


def _share(metric: str, steps: Sequence[Mapping[str, Measured]], what: str) -> Measured:
    """Share of the steps that measured ``metric`` with value 1 (not applicable: none did)."""
    decided = [values[metric].value or 0.0 for values in steps if values[metric].status is _OK]
    if not decided:
        return Measured.gap(_NA, f"no gated step measured {what}")
    numerator = float(sum(decided))
    return Measured(numerator / len(decided), numerator=numerator, denominator=float(len(decided)))


def _summed(metric: str, steps: Sequence[tuple[str, Mapping[str, Measured]]]) -> Measured:
    """Sum over the steps; unavailable when a step could not measure it."""
    gaps = [
        name for name, values in steps if values[metric].status in (_UNAVAILABLE, _INSUFFICIENT)
    ]
    if gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {', '.join(gaps)}")
    measured = [values[metric].value or 0.0 for _, values in steps if values[metric].status is _OK]
    if not measured:
        return Measured.gap(_NA, f"no gated step measured {metric}")
    return Measured(float(sum(measured)))


def _final(metric: str, steps: Sequence[tuple[str, Mapping[str, Measured]]]) -> Measured:
    if not steps:
        return Measured.gap(_INSUFFICIENT, "no gated step")
    name, values = steps[-1]
    measured = values[metric]
    if measured.status is _OK:
        return measured
    return Measured.gap(measured.status, f"at {name}: {measured.reason}")


def v6_trajectory_values(
    steps: Sequence[tuple[str, Mapping[str, Measured]]],
) -> dict[str, Measured]:
    measured = [values for _, values in steps]
    return {
        f"total_{NEW_FIRST_RUN_BLOCKING}": _summed(NEW_FIRST_RUN_BLOCKING, steps),
        f"final_{STEP_END_BLOCKING}": _final(STEP_END_BLOCKING, steps),
        f"total_{FIRST_RUN_UNTYPED_CALL}": _summed(FIRST_RUN_UNTYPED_CALL, steps),
        f"final_{STEP_END_UNTYPED_CALL}": _final(STEP_END_UNTYPED_CALL, steps),
        f"{FIRST_RUN_TESTS_FAILED}_rate": _share(
            FIRST_RUN_TESTS_FAILED, measured, "a first-run test result"
        ),
        f"{FIRST_RUN_COVERAGE_MET}_rate": _share(
            FIRST_RUN_COVERAGE_MET, measured, "first-run coverage"
        ),
        f"total_{FIRST_RUN_FAILED_TESTS}": _summed(FIRST_RUN_FAILED_TESTS, steps),
    }


def _observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    raw = [(step, _step_values(step, grades)) for step in steps]
    with_new_findings(raw)
    per_step = [(step, _adjusted(step, values, shadow, unchanged)) for step, values in raw]
    gated = [(step.inputs.step, values) for step, values in per_step if step.gated]
    totals = (
        trajectory_values(gated)
        | new_trajectory_values([values for _, values in gated])
        | v6_trajectory_values(gated)
    )
    if shadow:
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta"))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


class GateEffortV6Scorer(GateEffortV5Scorer):
    """gate-effort@5 with mode-comparable blocking counts and split tests outcomes."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V6

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V6, ctx, AssessStatus.ERROR, "gate-effort@6 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V6, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V6, ctx, AssessStatus.ERROR, reason)
        grades = _Grades(self._reader())
        observations = _observations(steps, modes == {True}, unchanged_steps(ctx), grades)
        self._missing = grades.missing
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else _INSUFFICIENT,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=evidence(steps),
        )


def gate_effort_v6_scorer() -> GateEffortV6Scorer:
    """Registry factory for ``gate-effort@6`` without a store (pre-fix grades unavailable)."""
    return GateEffortV6Scorer()


def gate_effort_v6_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV6Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV6Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V6",
    "GateEffortV6Scorer",
    "codes_once",
    "gate_effort_v6_scorer",
    "gate_effort_v6_scorer_for_store",
]
