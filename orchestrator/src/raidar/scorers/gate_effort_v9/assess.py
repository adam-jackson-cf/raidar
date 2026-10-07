"""``gate-effort@9``'s assessment of one trajectory (gate-effort@8's rules, @9's observations)."""

from __future__ import annotations

from raidar.scorers.gate_effort.scorer import _read
from raidar.scorers.gate_effort_v2.scorer import NO_GATE, _modes
from raidar.scorers.gate_effort_v3.scorer import unchanged_steps
from raidar.scorers.gate_effort_v4.scorer import GradeReader, _Grades
from raidar.scorers.gate_effort_v5.scorer import evidence
from raidar.scorers.gate_effort_v9.manifest import GATE_EFFORT_V9
from raidar.scorers.gate_effort_v9.trajectory import observations
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.result import AssessResult, AssessStatus

_OK = AssessStatus.OK


def assess_trajectory(
    ctx: AssessContext, reader: GradeReader | None
) -> tuple[AssessResult, list[str]]:
    """The trajectory's result, and the pre-fix grades it looked for and did not find."""
    if ctx.config:
        reason = "gate-effort@9 takes no config"
        return statused_result(GATE_EFFORT_V9, ctx, AssessStatus.ERROR, reason), []
    steps = [_read(inputs) for inputs in ctx.steps]
    if not any(step.gated for step in steps):
        return statused_result(GATE_EFFORT_V9, ctx, AssessStatus.NOT_APPLICABLE, NO_GATE), []
    modes = _modes(steps)
    if len(modes) > 1:
        reason = "the trajectory mixes shadow and enforced gate records"
        return statused_result(GATE_EFFORT_V9, ctx, AssessStatus.ERROR, reason), []
    grades = _Grades(reader)
    found = observations(steps, modes == {True}, unchanged_steps(ctx), grades)
    measured = any(item.status is _OK for item in found)
    result = AssessResult(
        status=_OK if measured else AssessStatus.INSUFFICIENT_EVIDENCE,
        reason=None if measured else "no gate run decided anything in this trajectory",
        observations=tuple(found),
        evidence=evidence(steps),
    )
    return result, grades.missing


__all__ = ["assess_trajectory"]
