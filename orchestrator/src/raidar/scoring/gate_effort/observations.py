"""``gate-effort``'s observations: every step's values and the trajectory's totals.

A shadow gate's fix-side totals are not applicable, the refusal split included; the
``new_autofix_*`` values apply to both gate modes.
"""

from __future__ import annotations

from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, Observation
from raidar.scoring.gate_effort.autofix.records import read_step_autofix
from raidar.scoring.gate_effort.autofix.step_added import with_new_autofix
from raidar.scoring.gate_effort.autofix.steps import with_autofix
from raidar.scoring.gate_effort.grades import Grades
from raidar.scoring.gate_effort.outcomes import Step
from raidar.scoring.gate_effort.per_step import per_step_values
from raidar.scoring.gate_effort.refusals import SPLIT_METRICS
from raidar.scoring.gate_effort.shadow import SHADOW_TOTAL_NA, shadowed
from raidar.scoring.gate_effort.totals import all_totals


def observations(
    steps: list[Step], shadow: bool, unchanged: frozenset[str], grades: Grades
) -> list[Observation]:
    found = with_autofix(per_step_values(steps, shadow, unchanged, grades), shadow, unchanged)
    blocks = {
        step.inputs.step: read_step_autofix(step.inputs.trace_dir) if step.inputs.reached else None
        for step, _ in found
    }
    with_new_autofix(found, blocks, shadow)
    totals = all_totals([(step.inputs.step, values) for step, values in found if step.gated])
    if shadow:
        split_totals = tuple(f"total_{name}" for name in SPLIT_METRICS)
        totals = shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta", *split_totals))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in found
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]
