"""``gate-effort@8``: gate-effort@7 counting a refused suppression comment once.

``gate-effort@7`` (``raidar.scorers.gate_effort_v7``) is released and unchanged.
In ``smoke-3b-3`` one refused ``# type: ignore`` counted as two blocking
findings in @7's cross-mode counts: the gate's ``SUPPRESS`` and mypy's
``MYPY.unused-ignore`` at the same line. @8 keeps every @7 metric, value,
status, reason and evidence item and adds the cross-mode counts with both
corrections, the ``no-untyped-call`` cascade left out (as in @7's
``*_ex_cascade``) and a ``MYPY.unused-ignore`` at a line that also has a
``SUPPRESS`` folded into it (``folding``):

- per step ``first_run_blocking_net``, ``step_end_blocking_net`` and
  ``new_first_run_blocking_net`` (the first one minus the previous gated step's
  step-end one, as @6 and @7 do);
- ``total_new_first_run_blocking_net`` and ``final_step_end_blocking_net``: the
  counts the pre-registered cross-mode comparison reads from iteration 3b.4 on.

A run whose folded findings cannot be located from the stored files makes the
step's net count ``unavailable`` (never a guess).
"""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.gate_effort.scorer import _read
from raidar.scorers.gate_effort_v2.scorer import NO_GATE, _modes
from raidar.scorers.gate_effort_v3.scorer import unchanged_steps
from raidar.scorers.gate_effort_v4.scorer import _Grades
from raidar.scorers.gate_effort_v5.scorer import evidence
from raidar.scorers.gate_effort_v7.scorer import GateEffortV7Scorer
from raidar.scorers.gate_effort_v8.net import GATE_EFFORT_V8, observations
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE


class GateEffortV8Scorer(GateEffortV7Scorer):
    """gate-effort@7 with the cross-mode counts counting a refused suppression comment once."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V8

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V8, ctx, AssessStatus.ERROR, "gate-effort@8 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V8, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V8, ctx, AssessStatus.ERROR, reason)
        grades = _Grades(self._reader())
        found = observations(steps, modes == {True}, unchanged_steps(ctx), grades)
        self._missing = grades.missing
        measured = any(item.status is _OK for item in found)
        return AssessResult(
            status=_OK if measured else _INSUFFICIENT,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(found),
            evidence=evidence(steps),
        )


def gate_effort_v8_scorer() -> GateEffortV8Scorer:
    """Registry factory for ``gate-effort@8`` without a store (pre-fix grades unavailable)."""
    return GateEffortV8Scorer()


def gate_effort_v8_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> GateEffortV8Scorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    del replay_only, trial
    return GateEffortV8Scorer(store_root)


__all__ = [
    "GATE_EFFORT_V8",
    "GateEffortV8Scorer",
    "gate_effort_v8_scorer",
    "gate_effort_v8_scorer_for_store",
]
