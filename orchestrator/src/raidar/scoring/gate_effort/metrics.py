"""``gate-effort``'s metric catalogue: effort, tests check and blocking counts.

The ids and descriptions are part of the scorer's identity (``gate_effort.manifest``). This
module holds the base effort metrics, the tests check and fix-round correctness, and the
blocking counts that compare across gate modes; ``cross_mode.metrics`` and ``autofix.metrics``
hold the rest.
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
from raidar.scoring.gate_effort.families import FAMILIES

_CHECKPOINT = MetricLevel.CHECKPOINT
_TRAJECTORY = MetricLevel.TRAJECTORY
_LOWER = Direction.LOWER_BETTER
_HIGHER = Direction.HIGHER_BETTER
_NEUTRAL = Direction.NEUTRAL

FIRST_RUN_BLOCKING = "first_run_blocking"
BLOCKING_FINDINGS = "blocking_findings"


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


def _families(prefix: str, what: str, level: MetricLevel) -> tuple[MetricSpec, ...]:
    return tuple(
        metric(f"{prefix}.{family}", f"{what}: {family} rules.", level, MetricType.COUNT, _LOWER)
        for family in FAMILIES
    )


EFFORT_STEP_METRICS = (
    metric(
        "gate_runs",
        "Gate runs in the step (one after each session).",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        "fix_rounds",
        "Fix sessions the gate's refusals started in the step.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        "first_pass_clean",
        "The gate passed on its first run (after the work session).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        "gate_clean_at_step_end",
        "The step's last gate run passed (manipulation-check signal).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        "unresolved",
        "The step used every fix round and the gate still refused.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    metric(
        "gate_error",
        "A gate run could not decide (infrastructure, never a fix round).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    metric(
        "rounds_to_clean",
        "Fix rounds before the gate passed; only for steps that ended clean.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        FIRST_RUN_BLOCKING,
        "Blocking findings of the step's first gate run.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(FIRST_RUN_BLOCKING, "Blocking findings of the first gate run", _CHECKPOINT),
    metric(
        BLOCKING_FINDINGS,
        "Blocking findings summed over the step's decided gate runs.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(BLOCKING_FINDINGS, "Blocking findings over every gate run", _CHECKPOINT),
    metric(
        "fix_input_tokens",
        "Input tokens of the step's fix sessions (work session excluded).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    metric(
        "fix_output_tokens",
        "Output tokens of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    metric(
        "fix_cost_usd",
        "Cost of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    metric(
        "fix_wall_s",
        "Wall time of the step's fix sessions.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
    metric(
        "gate_wall_s",
        "Wall time of the step's gate runs.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)

EFFORT_TRAJECTORY_METRICS = (
    metric(
        "gated_steps",
        "Reached checkpoints whose step has a gate record.",
        _TRAJECTORY,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        "first_pass_clean_rate",
        "Share of decided steps whose first gate run passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        "clean_at_step_end_rate",
        "Share of decided steps that ended with the gate passing.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        "unresolved_rate",
        "Share of decided steps that exhausted their fix rounds.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    metric(
        "gate_error_rate",
        "Share of steps with a gate run that a gate error ended.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    metric(
        "mean_rounds_to_clean",
        "Mean fix rounds before the gate passed, over clean steps.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        "total_fix_rounds",
        "Fix sessions over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        f"total_{FIRST_RUN_BLOCKING}",
        "First-run blocking findings over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(f"total_{FIRST_RUN_BLOCKING}", "First-run blocking findings", _TRAJECTORY),
    metric(
        f"total_{BLOCKING_FINDINGS}",
        "Blocking findings over every gate run of the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    *_families(f"total_{BLOCKING_FINDINGS}", "Blocking findings over every run", _TRAJECTORY),
    metric(
        "total_fix_input_tokens",
        "Fix-session input tokens over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    metric(
        "total_fix_output_tokens",
        "Fix-session output tokens over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
        "tokens",
    ),
    metric(
        "total_fix_cost_usd",
        "Fix-session cost over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "usd",
    ),
    metric(
        "total_fix_wall_s",
        "Fix-session wall time over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
    metric(
        "total_gate_wall_s",
        "Gate-run wall time over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
        "s",
    ),
)

TESTS_STEP_METRICS = (
    metric(
        "first_run_tests_passed",
        "The first gate run's tests check passed (the agent's own tests ran, passed, covered).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        "first_run_tests_missing",
        "The first gate run refused the change for having no test that ran.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    metric(
        "first_run_coverage",
        "Owned line coverage of the first gate run's test run.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    metric(
        "tests_passed_at_step_end",
        "The step's last decided gate run's tests check passed.",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        "tests_run_at_step_end",
        "Tests the step's last decided gate run ran.",
        _CHECKPOINT,
        MetricType.COUNT,
        _NEUTRAL,
    ),
    metric(
        "coverage_at_step_end",
        "Owned line coverage of the step's last decided gate run.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    metric(
        "pre_fix_pass_rate",
        "Case pass rate of the workspace the step's first fix session started from.",
        _CHECKPOINT,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        "correctness_delta",
        "The step's final case pass rate minus its pre-fix pass rate.",
        _CHECKPOINT,
        MetricType.REAL,
        _HIGHER,
    ),
)

TESTS_TRAJECTORY_METRICS = (
    metric(
        "first_run_tests_pass_rate",
        "Share of decided steps whose first gate run's tests check passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        "tests_missing_rate",
        "Share of decided steps whose first gate run found no test that ran.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    metric(
        "tests_pass_at_step_end_rate",
        "Share of decided steps whose last gate run's tests check passed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        "mean_first_run_coverage",
        "Mean owned line coverage of the first gate runs that ran tests.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    metric(
        "mean_coverage_at_step_end",
        "Mean owned line coverage at step end, over steps whose tests ran.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
        "percent",
    ),
    metric(
        "fix_round_correctness_delta",
        "Mean over steps with a fix round of final minus pre-fix case pass rate.",
        _TRAJECTORY,
        MetricType.REAL,
        _HIGHER,
    ),
)

STEP_END_BLOCKING = "step_end_blocking"
NEW_FIRST_RUN_BLOCKING = "new_first_run_blocking"
FIRST_RUN_UNTYPED_CALL = "first_run_untyped_call"
STEP_END_UNTYPED_CALL = "step_end_untyped_call"
FIRST_RUN_TESTS_FAILED = "first_run_tests_failed"
FIRST_RUN_COVERAGE_MET = "first_run_coverage_met"
FIRST_RUN_FAILED_TESTS = "first_run_failed_tests"

BLOCKING_STEP_METRICS = (
    metric(
        STEP_END_BLOCKING,
        "Blocking findings of the step's last decided gate run (a failed tests check once).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        NEW_FIRST_RUN_BLOCKING,
        "First-run blocking findings minus the previous gated step's step-end count.",
        _CHECKPOINT,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        FIRST_RUN_UNTYPED_CALL,
        "First-run MYPY.no-untyped-call findings (the call-site cascade of untyped defs).",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        STEP_END_UNTYPED_CALL,
        "MYPY.no-untyped-call findings of the step's last decided gate run.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        FIRST_RUN_TESTS_FAILED,
        "The first gate run's own tests failed or timed out (not applicable: no test ran).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _LOWER,
    ),
    metric(
        FIRST_RUN_COVERAGE_MET,
        "The first gate run's owned line coverage met the minimum (when measured).",
        _CHECKPOINT,
        MetricType.BOOLEAN,
        _HIGHER,
    ),
    metric(
        FIRST_RUN_FAILED_TESTS,
        "Failing tests in the first gate run's tests check.",
        _CHECKPOINT,
        MetricType.COUNT,
        _LOWER,
    ),
)

BLOCKING_TRAJECTORY_METRICS = (
    metric(
        f"total_{NEW_FIRST_RUN_BLOCKING}",
        "Blocking findings the steps' own work added before any fix, over the trajectory.",
        _TRAJECTORY,
        MetricType.REAL,
        _LOWER,
    ),
    metric(
        f"final_{STEP_END_BLOCKING}",
        "Blocking findings at the end of the last gated step.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        f"total_{FIRST_RUN_UNTYPED_CALL}",
        "First-run MYPY.no-untyped-call findings over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        f"final_{STEP_END_UNTYPED_CALL}",
        "MYPY.no-untyped-call findings at the end of the last gated step.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
    metric(
        f"{FIRST_RUN_TESTS_FAILED}_rate",
        "Share of steps whose first gate run ran tests in which they failed.",
        _TRAJECTORY,
        MetricType.RATIO,
        _LOWER,
    ),
    metric(
        f"{FIRST_RUN_COVERAGE_MET}_rate",
        "Share of steps whose first gate run measured coverage in which it met the minimum.",
        _TRAJECTORY,
        MetricType.RATIO,
        _HIGHER,
    ),
    metric(
        f"total_{FIRST_RUN_FAILED_TESTS}",
        "Failing tests in the first gate runs over the trajectory.",
        _TRAJECTORY,
        MetricType.COUNT,
        _LOWER,
    ),
)

RAW_STEP_METRICS = tuple(
    spec.id
    for spec in (*EFFORT_STEP_METRICS, *TESTS_STEP_METRICS, *BLOCKING_STEP_METRICS)
    if spec.level is _CHECKPOINT
)
"""The checkpoint metrics of the per-step values before the cross-mode and autofix additions."""
