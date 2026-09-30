"""Synthetic trajectories with known effects for analysis tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.analysis.outcomes import TrajectoryOutcome, Value
from raidar.analysis.report import AnalysisInputs, PlannedTrial

BASELINE = "baseline"
GUIDED = "guided"
STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3", "checkpoint_4")


def gap(status: str = "ineligible", reason: str = "synthetic gap") -> Value:
    return Value.missing(status, reason)


def trajectory(
    arm: str,
    block: int,
    metrics: Mapping[str, float | Value],
    *,
    problem: str = "toy@1",
    infra: bool = False,
    solved: Sequence[float | None] | None = None,
) -> TrajectoryOutcome:
    """One trajectory; plain floats become ``ok`` values, ``None`` checkpoints are gaps."""
    values = {
        name: value if isinstance(value, Value) else Value.ok(value)
        for name, value in metrics.items()
    }
    series = {}
    if solved is not None:
        series["solved"] = tuple(
            Value.ok(v) if v is not None else gap("not_applicable", "not reached") for v in solved
        )
    return TrajectoryOutcome(
        trial_id=f"{problem}-{arm}-b{block}",
        run_id="run-1",
        arm=arm,
        block=block,
        problem=problem,
        steps=STEPS,
        infra_error=infra,
        infra_reasons=("validity:steps_started",) if infra else (),
        metrics=values,
        series=series,
    )


def paired_arms(
    metric: str,
    baseline: Sequence[float],
    treatment: Sequence[float],
    *,
    cost: tuple[float, float] = (1.0, 1.5),
    problem: str = "toy@1",
) -> list[TrajectoryOutcome]:
    """Block ``i`` pairs ``baseline[i]`` with ``treatment[i]``; each arm has a fixed cost."""
    outcomes = []
    for block, (left, right) in enumerate(zip(baseline, treatment, strict=True)):
        outcomes.append(
            trajectory(BASELINE, block, {metric: left, "total_cost_usd": cost[0]}, problem=problem)
        )
        outcomes.append(
            trajectory(GUIDED, block, {metric: right, "total_cost_usd": cost[1]}, problem=problem)
        )
    return outcomes


def planned(blocks: int, problems: Sequence[str] = ("toy@1",)) -> tuple[PlannedTrial, ...]:
    return tuple(
        PlannedTrial(trial_id=f"{problem}-{arm}-b{block}", arm=arm, block=block, problem=problem)
        for problem in problems
        for block in range(blocks)
        for arm in (BASELINE, GUIDED)
    )


def inputs(
    outcomes: Sequence[TrajectoryOutcome],
    blocks: int,
    problems: Sequence[str] = ("toy@1",),
    progression: str = "continue",
) -> AnalysisInputs:
    return AnalysisInputs(
        experiment="synthetic",
        lock_sha256="0" * 64,
        runs=("run-1",),
        headline_policy={"ref": "scb-canonical@1", "selection": "default_by_k"},
        baseline_arm=BASELINE,
        progression=progression,
        planned=planned(blocks, problems),
        outcomes=tuple(outcomes),
    )
