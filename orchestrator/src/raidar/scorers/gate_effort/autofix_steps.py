"""Every step's values with the autofix step and the fix cost by refusal added.

The autofix values (``autofix_values``) and the fix-session split (``refusals``) are added to
each step's values; then the ``new_*`` counts that need the previous gated step.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.autofix_metrics import (
    FIRST_PASS_POST,
    FIRST_RUN_POST,
    FIRST_RUN_RAW,
    NEW_POST,
    NEW_RAW,
    STEP_END_POST,
)
from raidar.scorers.gate_effort.autofix_records import read_step_autofix
from raidar.scorers.gate_effort.autofix_values import autofix_step_values
from raidar.scorers.gate_effort.cross_mode_metrics import STEP_END_NET
from raidar.scorers.gate_effort.measured import Measured
from raidar.scorers.gate_effort.new_counts import PerStep, with_new
from raidar.scorers.gate_effort.outcomes import Step
from raidar.scorers.gate_effort.refusals import SPLIT_METRICS, refusal_values
from raidar.scorers.gate_effort.shadow import shadowed
from raidar.scorers.gate_effort.sources import NO_SOURCE_CHANGE


def _split_values(step: Step, values: dict[str, Measured]) -> dict[str, Measured]:
    if not step.inputs.reached:
        return dict.fromkeys(SPLIT_METRICS, Measured.not_applicable("checkpoint not reached"))
    if step.gate is None or step.error is not None:
        first = values["fix_cost_usd"]
        return dict.fromkeys(SPLIT_METRICS, Measured.gap(first.status, first.reason or ""))
    return refusal_values(step.gate)


def with_autofix(base: PerStep, shadow: bool, unchanged: frozenset[str]) -> PerStep:
    """``base`` (every step's values before autofix) plus the autofix values."""
    found: PerStep = []
    for step, values in base:
        autofix = read_step_autofix(step.inputs.trace_dir) if step.inputs.reached else None
        added = autofix_step_values(step, autofix, shadow, values) | _split_values(step, values)
        if shadow:
            added = shadowed(added, SPLIT_METRICS)
        no_edit = step.gate is not None and step.inputs.step in unchanged
        if no_edit and added[FIRST_PASS_POST].ok:
            added[FIRST_PASS_POST] = Measured.not_applicable(NO_SOURCE_CHANGE)
        found.append((step, values | added))
    with_new(found, FIRST_RUN_POST, STEP_END_POST, NEW_POST)
    with_new(found, FIRST_RUN_RAW, STEP_END_NET, NEW_RAW)
    return found
