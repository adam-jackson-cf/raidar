"""``gate-effort@9``'s per-step values: @8's, plus the autofix step and the fix cost by refusal.

``per_step`` runs @8's per-step pipeline (``gate_effort_v8.net``: @6's shadow
and no-edit rules applied) and adds, per step, the autofix values
(``autofix_values``) and the fix-session split (``refusals``); then the
``new_*`` counts that need the previous gated step (``with_new``).
"""

from __future__ import annotations

from raidar.scorers.gate_effort.scorer import Measured, _Step
from raidar.scorers.gate_effort_v2.scorer import _shadowed
from raidar.scorers.gate_effort_v3.scorer import NO_SOURCE_CHANGE
from raidar.scorers.gate_effort_v4.scorer import _Grades
from raidar.scorers.gate_effort_v8.net import STEP_END_NET, _per_step
from raidar.scorers.gate_effort_v9.autofix_records import read_step_autofix
from raidar.scorers.gate_effort_v9.autofix_values import autofix_step_values
from raidar.scorers.gate_effort_v9.manifest import (
    FIRST_PASS_POST,
    FIRST_RUN_POST,
    FIRST_RUN_RAW,
    NEW_POST,
    NEW_RAW,
    STEP_END_POST,
)
from raidar.scorers.gate_effort_v9.refusals import SPLIT_METRICS, refusal_values
from raidar.scorers.sdk.result import AssessStatus

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE

PerStep = list[tuple[_Step, dict[str, Measured]]]


def _split_values(step: _Step, values: dict[str, Measured]) -> dict[str, Measured]:
    if not step.inputs.reached:
        return dict.fromkeys(SPLIT_METRICS, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        first = values["fix_cost_usd"]
        return dict.fromkeys(SPLIT_METRICS, Measured.gap(first.status, first.reason or ""))
    return refusal_values(step.gate)


def with_new(per_step: PerStep, first_name: str, end_name: str, new_name: str) -> None:
    """``new_name`` = ``first_name`` minus the previous gated step's ``end_name`` (as @6-@8)."""
    previous = Measured(0.0)
    for step, values in per_step:
        if not step.inputs.reached:
            values[new_name] = Measured.gap(_NA, "checkpoint not reached")
            continue
        if not step.gated:
            previous = Measured.gap(_UNAVAILABLE, f"{step.inputs.step} has no gate record")
            continue
        first = values[first_name]
        if first.status is not _OK:
            values[new_name] = first
        elif previous.status is not _OK or previous.value is None:
            reason = f"the previous gated step's end count is unknown: {previous.reason}"
            values[new_name] = Measured.gap(_UNAVAILABLE, reason)
        else:
            values[new_name] = Measured((first.value or 0.0) - previous.value)
        previous = values[end_name]


def per_step(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> PerStep:
    """Every step's @8 values plus @9's (shadow and no-edit rules applied)."""
    found: PerStep = []
    for step, values in _per_step(steps, shadow, unchanged, grades):
        autofix = read_step_autofix(step.inputs.trace_dir) if step.inputs.reached else None
        added = autofix_step_values(step, autofix, shadow, values) | _split_values(step, values)
        if shadow:
            added = _shadowed(added, SPLIT_METRICS)
        no_edit = step.gate is not None and step.inputs.step in unchanged
        if no_edit and added[FIRST_PASS_POST].status is _OK:
            added[FIRST_PASS_POST] = Measured.gap(_NA, NO_SOURCE_CHANGE)
        found.append((step, values | added))
    with_new(found, FIRST_RUN_POST, STEP_END_POST, NEW_POST)
    with_new(found, FIRST_RUN_RAW, STEP_END_NET, NEW_RAW)
    return found


__all__ = ["PerStep", "per_step", "with_new"]
