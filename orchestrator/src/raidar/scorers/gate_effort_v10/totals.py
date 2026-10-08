"""``gate-effort@10``'s trajectory totals: @1-@9's, plus ``total_new_autofix_*``."""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scorers.gate_effort.scorer import Measured, trajectory_values
from raidar.scorers.gate_effort_v4.scorer import new_trajectory_values
from raidar.scorers.gate_effort_v6.scorer import _summed, v6_trajectory_values
from raidar.scorers.gate_effort_v7.scorer import v7_trajectory_values
from raidar.scorers.gate_effort_v8.net import v8_trajectory_values
from raidar.scorers.gate_effort_v9.trajectory import v9_trajectory_values
from raidar.scorers.gate_effort_v10.manifest import NEW_METRICS


def all_totals(gated: Sequence[tuple[str, dict[str, Measured]]]) -> dict[str, Measured]:
    """Every total over the gated steps (``gated``: each step's name and values)."""
    return (
        trajectory_values(gated)
        | new_trajectory_values([values for _, values in gated])
        | v6_trajectory_values(gated)
        | v7_trajectory_values(gated)
        | v8_trajectory_values(gated)
        | v9_trajectory_values(gated)
        | {f"total_{name}": _summed(name, gated) for name in NEW_METRICS}
    )


__all__ = ["all_totals"]
