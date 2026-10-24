"""``gate-effort``'s assessment of one trajectory."""

from __future__ import annotations

from raidar.scorers.gate_effort.evidence import evidence
from raidar.scorers.gate_effort.grades import GradeReader, Grades
from raidar.scorers.gate_effort.manifest import GATE_EFFORT
from raidar.scorers.gate_effort.observations import observations
from raidar.scorers.gate_effort.outcomes import read_step
from raidar.scorers.gate_effort.shadow import gate_modes
from raidar.scorers.gate_effort.sources import unchanged_steps
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.result import AssessResult, AssessStatus

_OK = AssessStatus.OK
NO_GATE = "no guardrail-gate record: this trajectory ran without a commit gate"


def assess_trajectory(
    ctx: AssessContext, reader: GradeReader | None
) -> tuple[AssessResult, list[str]]:
    if ctx.config:
        reason = f"gate-effort@{GATE_EFFORT.version} takes no config"
        return statused_result(GATE_EFFORT, ctx, AssessStatus.ERROR, reason), []
    steps = [read_step(inputs) for inputs in ctx.steps]
    if not any(step.gated for step in steps):
        return statused_result(GATE_EFFORT, ctx, AssessStatus.NOT_APPLICABLE, NO_GATE), []
    modes = gate_modes(steps)
    if len(modes) > 1:
        reason = "the trajectory mixes shadow and enforced gate records"
        return statused_result(GATE_EFFORT, ctx, AssessStatus.ERROR, reason), []
    grades = Grades(reader)
    found = observations(steps, modes == {True}, unchanged_steps(ctx), grades)
    measured = any(item.status is _OK for item in found)
    result = AssessResult(
        status=_OK if measured else AssessStatus.INSUFFICIENT_EVIDENCE,
        reason=None if measured else "no gate run decided anything in this trajectory",
        observations=tuple(found),
        evidence=evidence(steps),
    )
    return result, grades.missing
