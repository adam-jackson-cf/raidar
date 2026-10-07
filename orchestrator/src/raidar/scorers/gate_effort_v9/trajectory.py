"""``gate-effort@9``'s observations: every step's values and the trajectory's totals.

The totals are @1-@8's (``trajectory_values`` to ``v8_trajectory_values``)
plus @9's: ``total_<id>`` of each summed step metric, the post-autofix
first-pass rate and the final post-autofix step-end count. A shadow gate's
fix-side totals are not applicable (as from @2 on), the refusal split included.
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scorers.gate_effort.scorer import Measured, _Step, trajectory_values
from raidar.scorers.gate_effort_v2.scorer import SHADOW_TOTAL_NA, _shadowed
from raidar.scorers.gate_effort_v4.scorer import _Grades, new_trajectory_values
from raidar.scorers.gate_effort_v6.scorer import _final, _share, _summed, v6_trajectory_values
from raidar.scorers.gate_effort_v7.scorer import v7_trajectory_values
from raidar.scorers.gate_effort_v8.net import v8_trajectory_values
from raidar.scorers.gate_effort_v9.manifest import FIRST_PASS_POST, STEP_END_POST, V9_SUMMED
from raidar.scorers.gate_effort_v9.refusals import SPLIT_METRICS
from raidar.scorers.gate_effort_v9.steps import per_step
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, Observation


def v9_trajectory_values(steps: Sequence[tuple[str, dict[str, Measured]]]) -> dict[str, Measured]:
    measured = [values for _, values in steps]
    totals = {f"total_{name}": _summed(name, steps) for name in V9_SUMMED}
    return totals | {
        f"{FIRST_PASS_POST}_rate": _share(FIRST_PASS_POST, measured, "a check after autofix"),
        f"final_{STEP_END_POST}": _final(STEP_END_POST, steps),
    }


def observations(
    steps: list[_Step], shadow: bool, unchanged: frozenset[str], grades: _Grades
) -> list[Observation]:
    found = per_step(steps, shadow, unchanged, grades)
    gated = [(step.inputs.step, values) for step, values in found if step.gated]
    totals = (
        trajectory_values(gated)
        | new_trajectory_values([values for _, values in gated])
        | v6_trajectory_values(gated)
        | v7_trajectory_values(gated)
        | v8_trajectory_values(gated)
        | v9_trajectory_values(gated)
    )
    if shadow:
        split_totals = tuple(f"total_{name}" for name in SPLIT_METRICS)
        totals = _shadowed(totals, (*SHADOW_TOTAL_NA, "fix_round_correctness_delta", *split_totals))
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in found
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


__all__ = ["observations", "v9_trajectory_values"]
