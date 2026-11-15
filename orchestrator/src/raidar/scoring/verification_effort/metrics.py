"""``verification-effort``'s metric catalogue: what the ``gate/1`` checks cost the agent.

The ids and descriptions are part of the scorer's identity (``manifest``). A blocking check is a
``gate/1`` check the package declared blocking; a failed one is what makes the seal refuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from raidar.scoring.framework.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)

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


@dataclass(frozen=True)
class _Level:
    """Declares the deterministic, native metrics of one level."""

    level: MetricLevel

    def metric(
        self,
        metric_id: str,
        description: str,
        metric_type: MetricType,
        direction: Direction,
        unit: str | None = None,
    ) -> MetricSpec:
        return MetricSpec(
            id=metric_id,
            description=description,
            level=self.level,
            type=metric_type,
            direction=direction,
            tier=Tier.DETERMINISTIC,
            exactness=Exactness.NATIVE,
            unit=unit,
        )


_STEP = _Level(MetricLevel.CHECKPOINT)
_TRAJ = _Level(MetricLevel.TRAJECTORY)


STEP_METRICS = (
    _STEP.metric(
        "gate_runs", "Gate seals in the step (one after each turn).", MetricType.COUNT, _NEUTRAL
    ),
    _STEP.metric(
        FIX_ROUNDS, "Fix turns the gate's refusals started in the step.", MetricType.COUNT, _LOWER
    ),
    _STEP.metric(
        FIRST_PASS_CLEAN,
        "The gate's first seal passed (after the work turn).",
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _STEP.metric(
        CLEAN_AT_END,
        "The step's last gate seal passed (manipulation-check signal).",
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _STEP.metric(
        UNRESOLVED, "The step ended with its last gate seal refusing.", MetricType.BOOLEAN, _LOWER
    ),
    _STEP.metric(
        GATE_ERROR,
        "A gate seal could not decide (agent error or infrastructure).",
        MetricType.BOOLEAN,
        _LOWER,
    ),
    _STEP.metric(
        ROUNDS_TO_CLEAN,
        "Fix turns before the gate passed; only for steps that ended clean.",
        MetricType.COUNT,
        _LOWER,
    ),
    _STEP.metric(
        FIRST_RUN_BLOCKING,
        "Blocking checks that failed in the step's first gate seal.",
        MetricType.COUNT,
        _LOWER,
    ),
    _STEP.metric(
        BLOCKING_CHECKS,
        "Failed blocking checks summed over the step's decided gate seals.",
        MetricType.COUNT,
        _LOWER,
    ),
    _STEP.metric(
        "fix_input_tokens",
        "Input tokens of the step's fix turns (work turn excluded).",
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _STEP.metric(
        "fix_output_tokens",
        "Output tokens of the step's fix turns.",
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _STEP.metric("fix_cost_usd", "Cost of the step's fix turns.", MetricType.REAL, _LOWER, "usd"),
    _STEP.metric("fix_wall_s", "Wall time of the step's fix turns.", MetricType.REAL, _LOWER, "s"),
    _STEP.metric(GATE_WALL, "Wall time of the step's gate seals.", MetricType.REAL, _LOWER, "s"),
    _STEP.metric(
        PRE_FIX_PASS_RATE,
        "Case pass rate of the workspace the step's first fix turn started from.",
        MetricType.RATIO,
        _NEUTRAL,
    ),
    _STEP.metric(
        CORRECTNESS_DELTA,
        "The step's final case pass rate minus the pre-fix pass rate (a step with a fix turn).",
        MetricType.REAL,
        _HIGHER,
    ),
)

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
    _TRAJ.metric(
        "first_pass_clean_rate",
        "Share of decided gated steps whose first seal passed.",
        MetricType.RATIO,
        _HIGHER,
    ),
    _TRAJ.metric(
        "clean_at_step_end_rate",
        "Share of decided gated steps that ended with a passing seal.",
        MetricType.RATIO,
        _HIGHER,
    ),
    _TRAJ.metric(
        "unresolved_rate",
        "Share of decided gated steps that ended refused.",
        MetricType.RATIO,
        _LOWER,
    ),
    _TRAJ.metric(
        "gate_error_rate",
        "Share of decided gated steps with a seal that could not decide.",
        MetricType.RATIO,
        _LOWER,
    ),
    _TRAJ.metric(
        MEAN_ROUNDS,
        "Mean fix turns to clean over the steps that ended clean.",
        MetricType.REAL,
        _LOWER,
    ),
    _TRAJ.metric("total_fix_rounds", "Fix turns over the trajectory.", MetricType.COUNT, _LOWER),
    _TRAJ.metric(
        "total_first_run_blocking",
        "Failed blocking checks of the first seals, summed.",
        MetricType.COUNT,
        _LOWER,
    ),
    _TRAJ.metric(
        "total_blocking_checks",
        "Failed blocking checks over every decided seal, summed.",
        MetricType.COUNT,
        _LOWER,
    ),
    _TRAJ.metric(
        "total_fix_input_tokens",
        "Fix-turn input tokens over the trajectory.",
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _TRAJ.metric(
        "total_fix_output_tokens",
        "Fix-turn output tokens over the trajectory.",
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _TRAJ.metric(
        "total_fix_cost_usd", "Fix-turn cost over the trajectory.", MetricType.REAL, _LOWER, "usd"
    ),
    _TRAJ.metric(
        "total_fix_wall_s", "Fix-turn wall time over the trajectory.", MetricType.REAL, _LOWER, "s"
    ),
    _TRAJ.metric(
        "total_gate_wall_s",
        "Gate seal wall time over the trajectory.",
        MetricType.REAL,
        _LOWER,
        "s",
    ),
    _TRAJ.metric(
        FIX_DELTA,
        "Mean over steps with a fix turn of final minus pre-fix case pass rate.",
        MetricType.REAL,
        _HIGHER,
    ),
)
