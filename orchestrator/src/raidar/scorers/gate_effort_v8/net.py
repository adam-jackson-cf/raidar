"""``gate-effort@8``'s additions: the cross-mode counts with a refused suppression counted once.

Per step, ``first_run_blocking_net`` and ``step_end_blocking_net`` are @7's
``*_ex_cascade`` counts minus the run's folded findings (``folding``), and
``new_first_run_blocking_net`` is the first one minus the previous gated step's
step-end one; over the trajectory, ``total_new_first_run_blocking_net`` and
``final_step_end_blocking_net``. ``observations`` puts them beside every @7
value, with @6's shadow and no-edit rules applied as @7 applies them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.scorers.gate_effort.records import StepGate
from raidar.scorers.gate_effort.scorer import Measured, _Step, trajectory_values
from raidar.scorers.gate_effort_v2.scorer import SHADOW_TOTAL_NA, _shadowed
from raidar.scorers.gate_effort_v4.scorer import (
    FIRST_RUN_BLOCKING,
    _Grades,
    _metric,
    new_trajectory_values,
)
from raidar.scorers.gate_effort_v6.scorer import (
    _adjusted,
    _final,
    _step_values,
    _summed,
    v6_trajectory_values,
    with_new_findings,
)
from raidar.scorers.gate_effort_v7.scorer import (
    FIRST_RUN_EX_CASCADE,
    GATE_EFFORT_V7,
    STEP_END_EX_CASCADE,
    _minus,
    _no_edit,
    v7_step_values,
    v7_trajectory_values,
    with_new_ex_cascade,
)
from raidar.scorers.gate_effort_v8.folding import duplicates, raw_runs
from raidar.scorers.sdk.manifest import Direction, MetricLevel, MetricType
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus, Observation

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_LOWER = Direction.LOWER_BETTER

FIRST_RUN_NET = "first_run_blocking_net"
STEP_END_NET = "step_end_blocking_net"
NEW_NET = "new_first_run_blocking_net"
TOTAL_NEW_NET = f"total_{NEW_NET}"
FINAL_STEP_END_NET = f"final_{STEP_END_NET}"
_NET = (
    "without the MYPY.no-untyped-call cascade and with a refused suppression comment's "
    "MYPY.unused-ignore folded into its SUPPRESS"
)
LOCATIONS_UNKNOWN = "the run's folded findings cannot be located from the stored files"

V8_STEP_METRICS = (
    _metric(
        FIRST_RUN_NET, f"First-run blocking findings {_NET}.", _CHECKPOINT, MetricType.COUNT, _LOWER
    ),
    _metric(
        STEP_END_NET,
        f"Blocking findings of the step's last decided gate run {_NET}.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        NEW_NET,
        f"First-run blocking findings minus the previous gated step's step-end count, both {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
)
V8_TRAJECTORY_METRICS = (
    _metric(
        TOTAL_NEW_NET,
        f"Blocking findings the steps' own work added before any fix, over the trajectory, {_NET}.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        FINAL_STEP_END_NET,
        f"Blocking findings at the end of the last gated step {_NET}.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)
GATE_EFFORT_V8 = GATE_EFFORT_V7.model_copy(
    update={
        "version": "8",
        "description": (
            "Effort a guardrail-gate commit gate cost per step, the gate's tests check and "
            "the fix rounds' correctness (gate-effort@7: a failed tests check counted once, "
            "blocking counts that compare across gate modes with and without the mypy "
            "no-untyped-call cascade, failing tests and missed coverage apart, first-pass "
            "clean rates that ignore auto-fixable layout codes, the tests check, or both), plus "
            "the cross-mode blocking counts without the cascade and with a refused suppression "
            "comment counted once (mypy's unused-ignore at a SUPPRESS line folded into it). A "
            "shadow gate reports its first-run, step-end and code outcomes only. Not applicable "
            "to an arm without a gate."
        ),
        "metrics": (*GATE_EFFORT_V7.metrics, *V8_STEP_METRICS, *V8_TRAJECTORY_METRICS),
    }
)


# ---- one step ----------------------------------------------------------------------------


def _folded(step: _Step, gate: StepGate, index: int) -> Measured:
    """The folded findings of the step's gate run at ``index``."""
    trace_dir = step.inputs.trace_dir
    recorded = raw_runs(trace_dir)
    if trace_dir is None or len(recorded) != len(gate.runs):
        return Measured.gap(_UNAVAILABLE, LOCATIONS_UNKNOWN)
    found = duplicates(trace_dir, recorded[index], gate.runs[index].findings.codes)
    if found is None:
        return Measured.gap(_UNAVAILABLE, LOCATIONS_UNKNOWN)
    return Measured(float(found))


def _net(step: _Step, gate: StepGate, count: Measured, index: int | None) -> Measured:
    """``count`` (an @7 ``*_ex_cascade`` value) minus the folded findings of run ``index``."""
    if count.status is not _OK or index is None or not gate.runs:
        return count
    return _minus(count, _folded(step, gate, index))


def v8_step_values(step: _Step, values: Mapping[str, Measured]) -> dict[str, Measured]:
    """@8's per-step additions from @7's values (``new_*_net`` filled in over the trajectory)."""
    names = [metric.id for metric in V8_STEP_METRICS]
    if not step.inputs.reached:
        return dict.fromkeys(names, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        first = values[FIRST_RUN_BLOCKING]
        return dict.fromkeys(names, Measured.gap(first.status, first.reason or "no gate record"))
    decided = [index for index, run in enumerate(step.gate.runs) if run.decided]
    return {
        NEW_NET: Measured.gap(_UNAVAILABLE, "not computed"),
        FIRST_RUN_NET: _net(step, step.gate, values[FIRST_RUN_EX_CASCADE], 0),
        STEP_END_NET: _net(
            step, step.gate, values[STEP_END_EX_CASCADE], decided[-1] if decided else None
        ),
    }


def with_new_net(per_step: Sequence[tuple[_Step, dict[str, Measured]]]) -> None:
    """``new_first_run_blocking_net`` from the previous gated step's end (as @6 and @7 do)."""
    previous = Measured(0.0)
    for step, values in per_step:
        if not step.inputs.reached:
            values[NEW_NET] = Measured.gap(_NA, "checkpoint not reached")
            continue
        if not step.gated:
            previous = Measured.gap(_UNAVAILABLE, f"{step.inputs.step} has no gate record")
            continue
        first = values[FIRST_RUN_NET]
        if first.status is not _OK:
            values[NEW_NET] = first
        elif previous.status is not _OK or previous.value is None:
            reason = f"the previous gated step's end count is unknown: {previous.reason}"
            values[NEW_NET] = Measured.gap(_UNAVAILABLE, reason)
        else:
            values[NEW_NET] = Measured((first.value or 0.0) - previous.value)
        previous = values[STEP_END_NET]


# ---- the trajectory ----------------------------------------------------------------------


def v8_trajectory_values(
    steps: Sequence[tuple[str, Mapping[str, Measured]]],
) -> dict[str, Measured]:
    return {
        TOTAL_NEW_NET: _summed(NEW_NET, steps),
        FINAL_STEP_END_NET: _final(STEP_END_NET, steps),
    }


def _per_step(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[tuple[_Step, dict[str, Measured]]]:
    """@7's per-step values plus @8's, with @6's shadow and no-edit rules applied."""
    raw = [(step, _step_values(step, grades)) for step in steps]
    with_new_findings(raw)
    extended = [(step, values | v7_step_values(step, values)) for step, values in raw]
    with_new_ex_cascade(extended)
    netted = [(step, values | v8_step_values(step, values)) for step, values in extended]
    with_new_net(netted)
    per_step = []
    for step, values in netted:
        adjusted = _adjusted(step, values, shadow, unchanged)
        _no_edit(step, adjusted, unchanged)
        per_step.append((step, adjusted))
    return per_step


def observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    per_step = _per_step(steps, shadow, unchanged, grades)
    gated = [(step.inputs.step, values) for step, values in per_step if step.gated]
    totals = (
        trajectory_values(gated)
        | new_trajectory_values([values for _, values in gated])
        | v6_trajectory_values(gated)
        | v7_trajectory_values(gated)
        | v8_trajectory_values(gated)
    )
    if shadow:
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta"))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


__all__ = [
    "GATE_EFFORT_V8",
    "observations",
    "v8_step_values",
    "v8_trajectory_values",
    "with_new_net",
]
