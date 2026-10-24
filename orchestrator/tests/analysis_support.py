"""Synthetic trials with known effects for analysis tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.evaluation.analysis.outcomes import TrialOutcome, Value
from raidar.evaluation.analysis.report import AnalysisInputs, PlannedTrial

BASELINE = "baseline"
GUIDED = "guided"
STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3", "checkpoint_4")


def gap(status: str = "ineligible", reason: str = "synthetic gap") -> Value:
    return Value.missing(status, reason)


def trial(
    arm: str,
    attempt: int,
    metrics: Mapping[str, float | Value],
    *,
    problem: str = "toy@1",
    infra: bool = False,
    solved: Sequence[float | None] | None = None,
) -> TrialOutcome:
    """One trial; plain floats become ``ok`` values, ``None`` checkpoints are gaps."""
    values = {
        name: value if isinstance(value, Value) else Value.ok(value)
        for name, value in metrics.items()
    }
    series = {}
    if solved is not None:
        series["solved"] = tuple(
            Value.ok(v) if v is not None else gap("not_applicable", "not reached") for v in solved
        )
    return TrialOutcome(
        trial_id=f"{problem}-{arm}-b{attempt}",
        job_id="run-1",
        arm=arm,
        attempt=attempt,
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
) -> list[TrialOutcome]:
    """Attempt ``i`` pairs ``baseline[i]`` with ``treatment[i]``; each arm has a fixed cost."""
    outcomes = []
    for attempt, (left, right) in enumerate(zip(baseline, treatment, strict=True)):
        outcomes.append(
            trial(BASELINE, attempt, {metric: left, "total_cost_usd": cost[0]}, problem=problem)
        )
        outcomes.append(
            trial(GUIDED, attempt, {metric: right, "total_cost_usd": cost[1]}, problem=problem)
        )
    return outcomes


def planned(attempts: int, problems: Sequence[str] = ("toy@1",)) -> tuple[PlannedTrial, ...]:
    return tuple(
        PlannedTrial(
            trial_id=f"{problem}-{arm}-b{attempt}", arm=arm, attempt=attempt, problem=problem
        )
        for problem in problems
        for attempt in range(attempts)
        for arm in (BASELINE, GUIDED)
    )


def inputs(
    outcomes: Sequence[TrialOutcome],
    attempts: int,
    problems: Sequence[str] = ("toy@1",),
    progression: str = "continue",
) -> AnalysisInputs:
    return AnalysisInputs(
        experiment="synthetic",
        lock_sha256="0" * 64,
        jobs=("job-1",),
        metric={"ref": "scb-canonical@1", "selection": "default_by_k"},
        baseline_arm=BASELINE,
        progression=progression,
        planned=planned(attempts, problems),
        outcomes=tuple(outcomes),
    )
