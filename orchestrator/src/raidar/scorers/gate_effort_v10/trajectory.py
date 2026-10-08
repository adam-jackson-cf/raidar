"""``gate-effort@10``'s observations: @9's, plus the ``new_autofix_*`` values and their totals.

A shadow gate's fix-side totals are not applicable (as from @2 on), the refusal
split included; the ``new_autofix_*`` values apply to both gate modes.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.scorer import _Step
from raidar.scorers.gate_effort_v2.scorer import SHADOW_TOTAL_NA, _shadowed
from raidar.scorers.gate_effort_v4.scorer import _Grades
from raidar.scorers.gate_effort_v9.autofix_records import read_step_autofix
from raidar.scorers.gate_effort_v9.refusals import SPLIT_METRICS
from raidar.scorers.gate_effort_v9.steps import per_step
from raidar.scorers.gate_effort_v10.new_autofix import with_new_autofix
from raidar.scorers.gate_effort_v10.totals import all_totals
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, Observation


def observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    found = per_step(steps, shadow, unchanged, grades)
    blocks = {
        step.inputs.step: read_step_autofix(step.inputs.trace_dir) if step.inputs.reached else None
        for step, _ in found
    }
    with_new_autofix(found, blocks, shadow)
    totals = all_totals([(step.inputs.step, values) for step, values in found if step.gated])
    if shadow:
        split_totals = tuple(f"total_{name}" for name in SPLIT_METRICS)
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta", *split_totals))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in found
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


__all__ = ["observations"]
