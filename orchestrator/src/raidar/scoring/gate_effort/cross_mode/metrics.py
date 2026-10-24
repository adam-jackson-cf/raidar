"""``gate-effort``'s cross-mode metrics: first-pass variants and cascade-free counts.

Blocking counts without the ``MYPY.no-untyped-call`` cascade, and those with a refused
suppression comment's ``MYPY.unused-ignore`` folded into its ``SUPPRESS``. Ids and descriptions
are part of the scorer's identity (``gate_effort.manifest``).
"""

from __future__ import annotations

from raidar.scoring.framework.manifest import Direction, MetricLevel, MetricType
from raidar.scoring.gate_effort.metrics import metric

_CHECKPOINT, _TRAJECTORY = MetricLevel.CHECKPOINT, MetricLevel.TRAJECTORY
_HIGHER, _LOWER = Direction.HIGHER_BETTER, Direction.LOWER_BETTER

AUTOFIX_CODES = frozenset({"FORMAT", "I001", "W291", "W292", "W293"})
"""Blocking codes the gate's ``--fix`` resolves without changing behaviour (layout, imports)."""
FIRST_PASS_EXCEPT_AUTOFIX = "first_pass_clean_except_autofix"
FIRST_PASS_EXCEPT_TESTS = "first_pass_clean_except_tests"
FIRST_PASS_EXCEPT_BOTH = "first_pass_clean_except_autofix_tests"
FIRST_PASS_VARIANT_WHAT = {
    FIRST_PASS_EXCEPT_AUTOFIX: "auto-fixable layout codes",
    FIRST_PASS_EXCEPT_TESTS: "tests-check codes",
    FIRST_PASS_EXCEPT_BOTH: "auto-fixable layout or tests-check codes",
}
"""``{metric: what is ignored}``: the first run is clean but for those codes."""
FIRST_RUN_EX_CASCADE = "first_run_blocking_ex_cascade"
STEP_END_EX_CASCADE = "step_end_blocking_ex_cascade"
NEW_EX_CASCADE = "new_first_run_blocking_ex_cascade"
TOTAL_NEW_EX_CASCADE = f"total_{NEW_EX_CASCADE}"
FINAL_STEP_END_EX_CASCADE = f"final_{STEP_END_EX_CASCADE}"

EX_CASCADE_STEP_METRICS = (
    *(
        metric(
            name,
            f"The first gate run refused nothing but {what} "
            f"({', '.join(sorted(AUTOFIX_CODES))} are the auto-fixable codes).",
            _CHECKPOINT,
            MetricType.BOOLEAN,
            _HIGHER,
        )
        for name, what in FIRST_PASS_VARIANT_WHAT.items()
    ),
    metric(
        FIRST_RUN_EX_CASCADE,
        "First-run blocking findings without the MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        STEP_END_EX_CASCADE,
        "Blocking findings of the step's last decided gate run without the "
        "MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        NEW_EX_CASCADE,
        "First-run blocking findings minus the previous gated step's step-end count, both "
        "without the MYPY.no-untyped-call cascade.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
)

EX_CASCADE_TRAJECTORY_METRICS = (
    *(
        metric(
            f"{name}_rate",
            f"Share of decided steps whose first gate run refused nothing but {what}.",
            _TRAJECTORY,
            MetricType.RATIO,
            _HIGHER,
        )
        for name, what in FIRST_PASS_VARIANT_WHAT.items()
    ),
    metric(
        TOTAL_NEW_EX_CASCADE,
        "Blocking findings the steps' own work added before any fix, over the trajectory, "
        "without the MYPY.no-untyped-call cascade.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        FINAL_STEP_END_EX_CASCADE,
        "Blocking findings at the end of the last gated step without the "
        "MYPY.no-untyped-call cascade.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)

FIRST_RUN_NET = "first_run_blocking_net"
STEP_END_NET = "step_end_blocking_net"
NEW_NET = "new_first_run_blocking_net"
TOTAL_NEW_NET = f"total_{NEW_NET}"
FINAL_STEP_END_NET = f"final_{STEP_END_NET}"
_NET = (
    "without the MYPY.no-untyped-call cascade and with a refused suppression comment's "
    "MYPY.unused-ignore folded into its SUPPRESS"
)

NET_STEP_METRICS = (
    metric(
        FIRST_RUN_NET, f"First-run blocking findings {_NET}.", _CHECKPOINT, MetricType.COUNT, _LOWER
    ),
    metric(
        STEP_END_NET,
        f"Blocking findings of the step's last decided gate run {_NET}.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        NEW_NET,
        f"First-run blocking findings minus the previous gated step's step-end count, both {_NET}.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
)

NET_TRAJECTORY_METRICS = (
    metric(
        TOTAL_NEW_NET,
        f"Blocking findings the steps' own work added before any fix, over the trajectory, {_NET}.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        FINAL_STEP_END_NET,
        f"Blocking findings at the end of the last gated step {_NET}.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)
