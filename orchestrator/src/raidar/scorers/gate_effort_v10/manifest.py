"""``gate-effort@10``'s metrics: the autofix counts in a form that compares across gate modes.

Every ``gate-effort@9`` metric stays as it is; these are added (ids are part of
the scorer's identity).
"""

from __future__ import annotations

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort_v4.scorer import _metric
from raidar.scorers.gate_effort_v9.manifest import GATE_EFFORT_V9
from raidar.scorers.sdk.manifest import Direction, MetricLevel, MetricSpec, MetricType

_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_NEUTRAL = Direction.NEUTRAL

NEW_LINES = "new_autofix_lines_changed"
NEW_REMOVED = "new_autofix_removed_blocking"
NEW_FAMILIES = tuple(f"{NEW_REMOVED}.{family}" for family in FAMILIES)
NEW_METRICS = (NEW_LINES, NEW_REMOVED, *NEW_FAMILIES)
"""Step metrics whose trajectory value is ``total_<id>`` (the sum over gated steps)."""

_NEW = (
    "the step's first autofix pass on its own work (enforced: the pass before gate run 1; "
    "shadow: the dry run on the copy) minus what the previous gated step left for autofix "
    "(enforced: 0 when the pass before its last gate run completed, the tree it handed on "
    "being already fixed; shadow: its dry run, since the shadow tree is never fixed); "
    "signed, as gate-effort@6's new_first_run_blocking. A shadow gate's per-step autofix "
    "counts re-count the whole never-fixed tree at every step, so their sums are cumulative; "
    "this form's sum telescopes to the final tree's count and compares with an enforced "
    "gate's"
)

V10_STEP_METRICS: tuple[MetricSpec, ...] = (
    _metric(
        NEW_LINES,
        f"Lines the autofix changed that the step's own work added: {_NEW}.",
        _CHECKPOINT,
        MetricType.REAL,
        _NEUTRAL,
    ),
    _metric(
        NEW_REMOVED,
        f"Block-tier findings the autofix fixed that the step's own work added: {_NEW}.",
        _CHECKPOINT,
        MetricType.REAL,
        _NEUTRAL,
    ),
    *(
        _metric(
            name,
            f"Block-tier findings the autofix fixed that the step's own work added, in the "
            f"{name.rsplit('.', 1)[1]} family ({NEW_REMOVED}'s rule).",
            _CHECKPOINT,
            MetricType.REAL,
            _NEUTRAL,
        )
        for name in NEW_FAMILIES
    ),
)
_STEP_BY_ID = {metric.id: metric for metric in V10_STEP_METRICS}
V10_TRAJECTORY_METRICS: tuple[MetricSpec, ...] = tuple(
    _metric(
        f"total_{name}",
        f"{_STEP_BY_ID[name].description} Summed over the gated steps.",
        _TRAJECTORY,
        MetricType.REAL,
        _NEUTRAL,
    )
    for name in NEW_METRICS
)
GATE_EFFORT_V10 = GATE_EFFORT_V9.model_copy(
    update={
        "version": "10",
        "description": (
            f"{GATE_EFFORT_V9.description} Plus the autofix lines and block-tier findings "
            "that each step's own work added (new_autofix_*), which compare across an "
            "enforced and a shadow gate where the per-step autofix sums do not."
        ),
        "metrics": (*GATE_EFFORT_V9.metrics, *V10_STEP_METRICS, *V10_TRAJECTORY_METRICS),
    }
)

__all__ = [
    "GATE_EFFORT_V10",
    "NEW_FAMILIES",
    "NEW_LINES",
    "NEW_METRICS",
    "NEW_REMOVED",
    "V10_STEP_METRICS",
    "V10_TRAJECTORY_METRICS",
]
