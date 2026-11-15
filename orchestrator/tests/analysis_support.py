"""Synthetic trials with known effects for analysis tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from raidar.contract.trial import (
    ExecutionValidity,
    PlannedTrial as RecordedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.design.experiment.lock import LOCK_VERSION, seal_lock
from raidar.evaluation.analysis.outcomes import TrialOutcome, Value
from raidar.evaluation.analysis.report import AnalysisInputs, ArmSummary, PlannedTrial
from raidar.evaluation.analysis.sources import DevOptions
from raidar.evaluation.analysis.verification_effort import GateDescriptive
from raidar.scoring.catalog.outcomes import declared_outcome_types

BASELINE = "baseline"
GUIDED = "guided"
STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3", "checkpoint_4")
OUTCOME_TYPES = declared_outcome_types(
    dict.fromkeys(("toy@1", "a@1", "b@1", "oneshot@1", "multi@1"), len(STEPS))
)
"""What a lock declares for the synthetic problems: four checkpoints each."""


def gap(status: str = "ineligible", reason: str = "synthetic gap") -> Value:
    return Value.missing(status, reason)


def trial(
    arm: str,
    attempt: int,
    metrics: Mapping[str, float | Value],
    *,
    problem: str = "toy@1",
    infra: bool = False,
) -> TrialOutcome:
    """One trial; plain floats become ``ok`` values."""
    values = {
        name: value if isinstance(value, Value) else Value.ok(value)
        for name, value in metrics.items()
    }
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
    )


def checkpoint_trial(
    arm: str, attempt: int, solved: Sequence[float | None], *, infra: bool = False
) -> TrialOutcome:
    """One trial with no metrics and a ``solved`` series; ``None`` checkpoints are gaps."""
    points = tuple(
        Value.ok(v) if v is not None else gap("not_applicable", "not reached") for v in solved
    )
    outcome: TrialOutcome = trial(arm, attempt, {}, infra=infra).model_copy(
        update={"series": {"solved": points}}
    )
    return outcome


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


def arm_summary(name: str, verification_effort: Mapping[str, float]) -> ArmSummary:
    """One valid trial's arm summary carrying the given verification-effort means."""
    return ArmSummary(
        arm=name,
        planned=1,
        run=1,
        valid=1,
        infra_error=0,
        not_run=0,
        infra_reasons={},
        cost_means={},
        verification_effort={
            metric: GateDescriptive(n=1, mean=value, sd=None, interval=None)
            for metric, value in verification_effort.items()
        },
    )


def analysis_lock(
    scorers: list[str],
    *,
    experiment: str = "pilot",
    headline: str = "scb-canonical@1",
    **thresholds: Any,
) -> dict[str, Any]:
    """A sealed lock for the ``toy`` problem of two checkpoints, built in memory."""
    analysis = {"baseline_arm": "baseline", **thresholds}
    spec = {
        "kind": "ab-test",
        "runtime": "python",
        "analysis": {**analysis, "attempts": 5, "seed": 11},
        "assessment": {"scorers": scorers},
        "protocol": {"progression": "continue"},
    }
    return seal_lock(
        {
            "lock_version": LOCK_VERSION,
            "experiment": {"id": experiment},
            "spec": spec,
            "execution": {"design": {"id": experiment, "attempts": 5, "seed": 11}},
            "assessment": {
                "spec": spec["assessment"],
                "metric": {"ref": headline, "selection": "default_by_k", "checkpoints": [2]},
            },
            "analysis": {**analysis, "outcome_types": declared_outcome_types({"scb/toy@1": 2})},
            "toolchain": {},
        }
    )


RECORD_STEPS = ("checkpoint_1", "checkpoint_2")
RECORD_HASHES = TrialHashes(
    lock="l",
    problem="p",
    arm="a",
    workflow="w",
    packages="s",
    primitive_code="s",
    agent="g",
    protocol="r",
    trial="t",
)


def trial_record(
    trial_id: str,
    arm: str,
    attempt: int,
    *,
    valid: bool = True,
    options: DevOptions | None = None,
) -> TrialRecord:
    """A trial record built in memory that planned ``RECORD_STEPS`` and ran under ``options``."""
    options = options or DevOptions()
    return TrialRecord(
        experiment="pilot",
        job_id="run-1",
        trial=RecordedTrial(
            trial_id=trial_id,
            attempt=attempt,
            order=0,
            sequence=attempt,
            arm=arm,
            problem="scb/toy@1",
            problem_index=0,
            steps=RECORD_STEPS,
            hashes=RECORD_HASHES,
        ),
        trial_dir=f"trials/{trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="solved"),
        truncated_to=options.max_checkpoints,
        agent_override=options.agent_override,
        step_results=(),
        reached=tuple(True for _ in RECORD_STEPS),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="steps_started", passed=valid),)),
    )
