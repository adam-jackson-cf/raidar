"""``verification-effort``'s metric catalogue: what the ``gate/1`` checks cost the agent.

The ids and descriptions are part of the scorer's identity (``manifest``). A blocking check is a
``gate/1`` check the package declared blocking; a failed one is what makes the seal refuse.
"""

from __future__ import annotations

from raidar.scoring.framework.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)

_CHECKPOINT = MetricLevel.CHECKPOINT
_TRAJECTORY = MetricLevel.TRAJECTORY
_LOWER = Direction.LOWER_BETTER
_HIGHER = Direction.HIGHER_BETTER
_NEUTRAL = Direction.NEUTRAL

FIRST_RUN_BLOCKING = "first_run_blocking"
BLOCKING_CHECKS = "blocking_checks"
FIRST_PASS_CLEAN = "first_pass_clean"
CLEAN_AT_END = "gate_clean_at_step_end"
UNRESOLVED = "unresolved"
GATE_ERROR = "gate_error"
FIX_ROUNDS = "fix_rounds"
ROUNDS_TO_CLEAN = "rounds_to_clean"
PRE_FIX_PASS_RATE = "pre_fix_pass_rate"
CORRECTNESS_DELTA = "correctness_delta"
GATE_WALL = "gate_wall_s"


def metric(
    metric_id: str,
    description: str,
    level: MetricLevel,
    metric_type: MetricType,
    direction: Direction,
    unit: str | None = None,
) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=description,
        level=level,
        type=metric_type,
        direction=direction,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
        unit=unit,
    )


STEP_METRICS = (
    metric("gate_runs", "Gate seals in the step (one after each turn).", _CHECKPOINT,
           MetricType.COUNT, _NEUTRAL),
    metric(FIX_ROUNDS, "Fix turns the gate's refusals started in the step.", _CHECKPOINT,
           MetricType.COUNT, _LOWER),
    metric(FIRST_PASS_CLEAN, "The gate's first seal passed (after the work turn).", _CHECKPOINT,
           MetricType.BOOLEAN, _HIGHER),
    metric(CLEAN_AT_END, "The step's last gate seal passed (manipulation-check signal).",
           _CHECKPOINT, MetricType.BOOLEAN, _HIGHER),
    metric(UNRESOLVED, "The step ended with its last gate seal refusing.", _CHECKPOINT,
           MetricType.BOOLEAN, _LOWER),
    metric(GATE_ERROR, "A gate seal could not decide (agent error or infrastructure).",
           _CHECKPOINT, MetricType.BOOLEAN, _LOWER),
    metric(ROUNDS_TO_CLEAN, "Fix turns before the gate passed; only for steps that ended clean.",
           _CHECKPOINT, MetricType.COUNT, _LOWER),
    metric(FIRST_RUN_BLOCKING, "Blocking checks that failed in the step's first gate seal.",
           _CHECKPOINT, MetricType.COUNT, _LOWER),
    metric(BLOCKING_CHECKS, "Failed blocking checks summed over the step's decided gate seals.",
           _CHECKPOINT, MetricType.COUNT, _LOWER),
    metric("fix_input_tokens", "Input tokens of the step's fix turns (work turn excluded).",
           _CHECKPOINT, MetricType.COUNT, _LOWER, "tokens"),
    metric("fix_output_tokens", "Output tokens of the step's fix turns.", _CHECKPOINT,
           MetricType.COUNT, _LOWER, "tokens"),
    metric("fix_cost_usd", "Cost of the step's fix turns.", _CHECKPOINT, MetricType.REAL,
           _LOWER, "usd"),
    metric("fix_wall_s", "Wall time of the step's fix turns.", _CHECKPOINT, MetricType.REAL,
           _LOWER, "s"),
    metric(GATE_WALL, "Wall time of the step's gate seals.", _CHECKPOINT, MetricType.REAL,
           _LOWER, "s"),
    metric(PRE_FIX_PASS_RATE,
           "Case pass rate of the workspace the step's first fix turn started from.",
           _CHECKPOINT, MetricType.RATIO, _NEUTRAL),
    metric(CORRECTNESS_DELTA,
           "The step's final case pass rate minus the pre-fix pass rate (a step with a fix turn).",
           _CHECKPOINT, MetricType.REAL, _HIGHER),
)  # fmt: skip

STEP_IDS = tuple(item.id for item in STEP_METRICS)

RATES = {
    "first_pass_clean_rate": FIRST_PASS_CLEAN,
    "clean_at_step_end_rate": CLEAN_AT_END,
    "unresolved_rate": UNRESOLVED,
    "gate_error_rate": GATE_ERROR,
}
"""``{trajectory id: step metric}``: the share of the steps that decided the metric with 1."""
SUMMED = {
    "total_fix_rounds": FIX_ROUNDS,
    "total_first_run_blocking": FIRST_RUN_BLOCKING,
    "total_blocking_checks": BLOCKING_CHECKS,
    "total_fix_input_tokens": "fix_input_tokens",
    "total_fix_output_tokens": "fix_output_tokens",
    "total_fix_cost_usd": "fix_cost_usd",
    "total_fix_wall_s": "fix_wall_s",
    "total_gate_wall_s": GATE_WALL,
}
"""``{trajectory id: step metric}``: the sum over the gated steps."""
MEAN_ROUNDS = "mean_rounds_to_clean"
FIX_DELTA = "fix_round_correctness_delta"

TRAJECTORY_METRICS = (
    metric("first_pass_clean_rate", "Share of decided gated steps whose first seal passed.",
           _TRAJECTORY, MetricType.RATIO, _HIGHER),
    metric("clean_at_step_end_rate", "Share of decided gated steps that ended with a passing seal.",
           _TRAJECTORY, MetricType.RATIO, _HIGHER),
    metric("unresolved_rate", "Share of decided gated steps that ended refused.", _TRAJECTORY,
           MetricType.RATIO, _LOWER),
    metric("gate_error_rate", "Share of decided gated steps with a seal that could not decide.",
           _TRAJECTORY, MetricType.RATIO, _LOWER),
    metric(MEAN_ROUNDS, "Mean fix turns to clean over the steps that ended clean.", _TRAJECTORY,
           MetricType.REAL, _LOWER),
    metric("total_fix_rounds", "Fix turns over the trajectory.", _TRAJECTORY, MetricType.COUNT,
           _LOWER),
    metric("total_first_run_blocking", "Failed blocking checks of the first seals, summed.",
           _TRAJECTORY, MetricType.COUNT, _LOWER),
    metric("total_blocking_checks", "Failed blocking checks over every decided seal, summed.",
           _TRAJECTORY, MetricType.COUNT, _LOWER),
    metric("total_fix_input_tokens", "Fix-turn input tokens over the trajectory.", _TRAJECTORY,
           MetricType.COUNT, _LOWER, "tokens"),
    metric("total_fix_output_tokens", "Fix-turn output tokens over the trajectory.", _TRAJECTORY,
           MetricType.COUNT, _LOWER, "tokens"),
    metric("total_fix_cost_usd", "Fix-turn cost over the trajectory.", _TRAJECTORY,
           MetricType.REAL, _LOWER, "usd"),
    metric("total_fix_wall_s", "Fix-turn wall time over the trajectory.", _TRAJECTORY,
           MetricType.REAL, _LOWER, "s"),
    metric("total_gate_wall_s", "Gate seal wall time over the trajectory.", _TRAJECTORY,
           MetricType.REAL, _LOWER, "s"),
    metric(FIX_DELTA,
           "Mean over steps with a fix turn of final minus pre-fix case pass rate.",
           _TRAJECTORY, MetricType.REAL, _HIGHER),
)  # fmt: skip
