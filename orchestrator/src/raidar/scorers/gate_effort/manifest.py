"""Manifest of ``gate-effort@1``: how much work a commit gate cost the agent, per step.

It lives beside its implementation (as ``process-compliance@2`` does) so the
released scorers' digests, which cover ``builtin_manifests``, stay unchanged.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.sdk.manifest import (
    Direction,
    EvidenceInput,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)

_CHECKPOINT = MetricLevel.CHECKPOINT
_TRAJECTORY = MetricLevel.TRAJECTORY
_LOWER = Direction.LOWER_BETTER
_HIGHER = Direction.HIGHER_BETTER
_NEUTRAL = Direction.NEUTRAL

FIRST_RUN_BLOCKING = "first_run_blocking"
BLOCKING_FINDINGS = "blocking_findings"


def _metric(
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


def _families(prefix: str, what: str, level: MetricLevel) -> tuple[MetricSpec, ...]:
    return tuple(
        _metric(f"{prefix}.{family}", f"{what}: {family} rules.", level, MetricType.COUNT, _LOWER)
        for family in FAMILIES
    )


_STEP = (
    _metric(
        "gate_runs",
        "Gate runs in the step (one after each session).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        "fix_rounds",
        "Fix sessions the gate's refusals started in the step.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        "first_pass_clean",
        "The gate passed on its first run (after the work session).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        "gate_clean_at_step_end",
        "The step's last gate run passed (manipulation-check signal).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    _metric(
        "unresolved",
        "The step used every fix round and the gate still refused.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    _metric(
        "gate_error",
        "A gate run could not decide (infrastructure, never a fix round).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    _metric(
        "rounds_to_clean",
        "Fix rounds before the gate passed; only for steps that ended clean.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        FIRST_RUN_BLOCKING,
        "Blocking findings of the step's first gate run.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(FIRST_RUN_BLOCKING, "Blocking findings of the first gate run", _CHECKPOINT),
    _metric(
        BLOCKING_FINDINGS,
        "Blocking findings summed over the step's decided gate runs.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(BLOCKING_FINDINGS, "Blocking findings over every gate run", _CHECKPOINT),
    _metric(
        "fix_input_tokens",
        "Input tokens of the step's fix sessions (work session excluded).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _metric(
        "fix_output_tokens",
        "Output tokens of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _metric(
        "fix_cost_usd",
        "Cost of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    _metric(
        "fix_wall_s",
        "Wall time of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
    _metric(
        "gate_wall_s",
        "Wall time of the step's gate runs.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)

_TOTALS = (
    _metric(
        "gated_steps",
        "Reached checkpoints whose step has a gate record.",
        _TRAJECTORY,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    _metric(
        "first_pass_clean_rate",
        "Share of decided steps whose first gate run passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        "clean_at_step_end_rate",
        "Share of decided steps that ended with the gate passing.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    _metric(
        "unresolved_rate",
        "Share of decided steps that exhausted their fix rounds.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    _metric(
        "gate_error_rate",
        "Share of steps with a gate run that a gate error ended.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    _metric(
        "mean_rounds_to_clean",
        "Mean fix rounds before the gate passed, over clean steps.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    _metric(
        "total_fix_rounds",
        "Fix sessions over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    _metric(
        f"total_{FIRST_RUN_BLOCKING}",
        "First-run blocking findings over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(f"total_{FIRST_RUN_BLOCKING}", "First-run blocking findings", _TRAJECTORY),
    _metric(
        f"total_{BLOCKING_FINDINGS}",
        "Blocking findings over every gate run of the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(f"total_{BLOCKING_FINDINGS}", "Blocking findings over every run", _TRAJECTORY),
    _metric(
        "total_fix_input_tokens",
        "Fix-session input tokens over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _metric(
        "total_fix_output_tokens",
        "Fix-session output tokens over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    _metric(
        "total_fix_cost_usd",
        "Fix-session cost over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    _metric(
        "total_fix_wall_s",
        "Fix-session wall time over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
    _metric(
        "total_gate_wall_s",
        "Gate-run wall time over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)

GATE_EFFORT = ScorerManifest(
    id="gate-effort",
    version="1",
    description=(
        "Effort a guardrail-gate@1 commit gate cost per step: rounds to clean, first-pass "
        "clean, unresolved and gate-error steps, blocking findings by rule family, and the "
        "fix sessions' tokens, cost and time. Not applicable to an arm without the gate."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE,),
    metrics=(*_STEP, *_TOTALS),
)
