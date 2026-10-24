"""``resource-usage@1``: tokens, cost, turns and agent wall time (plan G4).

Per checkpoint, the values come from Harbor as it recorded the step:

- ``input_tokens``, ``output_tokens`` and ``cost_usd`` are the step's
  ``agent_result`` (``n_input_tokens``, which includes cached input,
  ``n_output_tokens`` and ``cost_usd``). They already cover every session the
  agent ran in the step, so a review loop's reviewer sessions count for the
  arm that ran them.
- ``turns`` counts agent-sourced steps over every session in the step's ATIF
  trajectory, including embedded sub-agent sessions.
- ``agent_wall_s`` is the duration of Harbor's ``agent_execution`` phase.

A value Harbor did not report stays unreported: the observation is
``unavailable``, never zero. A checkpoint the trial never reached is
``not_applicable``. Trajectory totals sum the reached checkpoints, so they
reconcile exactly with the sum of the steps' ``agent_result``; a total is
``unavailable`` when any reached checkpoint lacks its value.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from raidar.scoring.framework.atif import Trajectory, TrajectoryError, load_trajectory
from raidar.scoring.framework.builtin_manifests import RESOURCE_USAGE
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Span,
)

_USAGE_FIELDS = {
    "input_tokens": "n_input_tokens",
    "output_tokens": "n_output_tokens",
    "cost_usd": "cost_usd",
}
_TOTALS = {
    "total_tokens": ("input_tokens", "output_tokens"),
    "total_cost_usd": ("cost_usd",),
    "total_turns": ("turns",),
    "total_agent_wall_s": ("agent_wall_s",),
}


@dataclass(frozen=True)
class Measured:
    """A checkpoint value, or why there is none."""

    value: float | None
    status: AssessStatus = AssessStatus.OK
    reason: str | None = None


def _unavailable(reason: str) -> Measured:
    return Measured(None, AssessStatus.UNAVAILABLE, reason)


def _usage(inputs: StepInputs, field: str) -> Measured:
    usage = inputs.agent_result
    value = usage.get(field) if usage is not None else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return _unavailable(f"Harbor did not report {field} for this step")
    return Measured(float(value))


def _trajectory(inputs: StepInputs) -> Trajectory | Measured:
    try:
        trajectory = load_trajectory(inputs.trace_dir)
    except TrajectoryError as exc:
        return _unavailable(f"trajectory unreadable: {exc}")
    if trajectory is None:
        return _unavailable("the step has no ATIF trajectory")
    return trajectory


def _step_trace(inputs: StepInputs) -> Trajectory | Measured:
    if not inputs.reached:
        return Measured(None, AssessStatus.NOT_APPLICABLE, "checkpoint not reached")
    return _trajectory(inputs)


def _turns(trajectory: Trajectory | Measured) -> Measured:
    if isinstance(trajectory, Measured):
        return trajectory
    return Measured(float(trajectory.agent_turns))


def _wall(inputs: StepInputs) -> Measured:
    if inputs.agent_execution_s is None:
        return _unavailable("Harbor did not record the step's agent_execution timing")
    return Measured(inputs.agent_execution_s)


def checkpoint_values(inputs: StepInputs, trajectory: Trajectory | Measured) -> dict[str, Measured]:
    """Every checkpoint metric of one step, keyed by metric id."""
    if not inputs.reached:
        skipped = Measured(None, AssessStatus.NOT_APPLICABLE, "checkpoint not reached")
        return dict.fromkeys((*_USAGE_FIELDS, "turns", "agent_wall_s"), skipped)
    values = {metric: _usage(inputs, field) for metric, field in _USAGE_FIELDS.items()}
    values["turns"] = _turns(trajectory)
    values["agent_wall_s"] = _wall(inputs)
    return values


def _observation(metric: str, subject: str, measured: Measured) -> Observation:
    return Observation(
        metric=metric,
        subject=subject,
        status=measured.status,
        value=measured.value,
        reason=measured.reason,
    )


def _parts(parts: Sequence[str], steps: Sequence[dict[str, Measured]]) -> list[Measured]:
    """The ``parts`` values of every reached checkpoint (unreached ones are not applicable)."""
    return [
        values[part]
        for values in steps
        if values[parts[0]].status is not AssessStatus.NOT_APPLICABLE
        for part in parts
    ]


def total(parts: Sequence[str], steps: Sequence[dict[str, Measured]]) -> Measured:
    """Sum of ``parts`` over the reached checkpoints; unavailable if any value is missing."""
    values = _parts(parts, steps)
    if not values:
        return Measured(None, AssessStatus.NOT_APPLICABLE, "no checkpoint was reached")
    if any(value.value is None for value in values):
        return _unavailable(f"a reached checkpoint has no {' or '.join(parts)}")
    return Measured(sum(value.value or 0.0 for value in values))


def _turns_evidence(inputs: StepInputs, trajectory: Trajectory | Measured) -> Evidence | None:
    if not isinstance(trajectory, Trajectory):
        return None
    data = trajectory.path.read_bytes()
    # The validator counts newline-terminated lines; str.splitlines also splits on U+2028 etc.
    lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    if lines == 0:
        return None
    return Evidence(
        metric="turns",
        subject=inputs.step,
        source=EvidenceSource.TRACE,
        path=trajectory.path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        kind="atif-trajectory",
        spans=(Span(line=1, end_line=lines),),
        message=f"{trajectory.agent_turns} agent turns over {len(trajectory.steps)} ATIF steps",
    )


def _observations(per_step: Sequence[tuple[StepInputs, dict[str, Measured]]]) -> list[Observation]:
    """Checkpoint observations in step order, then the trajectory totals."""
    observations = [
        _observation(metric, step.step, measured)
        for step, values in per_step
        for metric, measured in values.items()
    ]
    steps = [values for _, values in per_step]
    return observations + [
        _observation(metric, TRAJECTORY_SUBJECT, total(parts, steps))
        for metric, parts in _TOTALS.items()
    ]


class ResourceUsageScorer:
    """Deterministic scorer over Harbor's per-step usage, timing and ATIF trace."""

    @property
    def manifest(self) -> ScorerManifest:
        return RESOURCE_USAGE

    def assess(self, ctx: AssessContext) -> AssessResult:
        traces = [(step, _step_trace(step)) for step in ctx.steps]
        observations = _observations(
            [(step, checkpoint_values(step, trace)) for step, trace in traces]
        )
        evidence = tuple(filter(None, (_turns_evidence(step, trace) for step, trace in traces)))
        measured = any(item.status is AssessStatus.OK for item in observations)
        return AssessResult(
            status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
            reason=None if measured else "Harbor reported no usage or timing for any step",
            observations=tuple(observations),
            evidence=evidence,
        )


def resource_usage_scorer() -> ResourceUsageScorer:
    """Registry factory for ``resource-usage@1``."""
    return ResourceUsageScorer()
