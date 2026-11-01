"""``verification-effort``'s assessment of one trajectory."""

from __future__ import annotations

from raidar.scoring.framework.context import AssessContext, statused_result
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.scoring.verification_effort.grades import GradeReader, Grades
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT
from raidar.scoring.verification_effort.sources import unchanged_steps
from raidar.scoring.verification_effort.steps import Step, read_step, step_values
from raidar.scoring.verification_effort.totals import trajectory_values

_OK = AssessStatus.OK
NO_GATE = "no gate/1 record: this trajectory ran without a gate/1 package"


def _observations(ctx: AssessContext, steps: list[Step], grades: Grades) -> list[Observation]:
    unchanged = unchanged_steps(ctx)
    found = [(step, step_values(step, unchanged, grades)) for step in steps]
    totals = trajectory_values([(step.inputs.step, values) for step, values in found])
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in found
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


def assess_trajectory(
    ctx: AssessContext, reader: GradeReader | None
) -> tuple[AssessResult, list[str]]:
    """The result, and the pre-fix snapshots whose grades are still missing."""
    if ctx.config:
        reason = f"{VERIFICATION_EFFORT.ref} takes no config"
        return statused_result(VERIFICATION_EFFORT, ctx, AssessStatus.ERROR, reason), []
    steps = [read_step(inputs) for inputs in ctx.steps]
    if not any(step.gated for step in steps):
        return statused_result(VERIFICATION_EFFORT, ctx, AssessStatus.NOT_APPLICABLE, NO_GATE), []
    grades = Grades(reader)
    observations = _observations(ctx, steps, grades)
    measured = any(item.status is _OK for item in observations)
    result = AssessResult(
        status=_OK if measured else AssessStatus.INSUFFICIENT_EVIDENCE,
        reason=None if measured else "no gate seal decided anything in this trajectory",
        observations=tuple(observations),
    )
    return result, grades.missing
