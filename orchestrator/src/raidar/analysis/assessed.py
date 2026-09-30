"""Reduce one trajectory's record and Assessments to a ``TrajectoryOutcome``.

Inputs are the run's ``TrajectoryRecord`` (validity and identity) and the
stored ``AssessResult`` of each scorer (``None`` when the store has no
Assessment for this trajectory). Nothing is recomputed from raw run files:
correctness comes from ``correctness@1``, quality from ``code-quality@1``,
cost from ``resource-usage@1``, and the headline score from the experiment's
ScorePolicy applied to those components.

Two derivations are analysis-owned:

- ``final_solved`` under ``halt_on_failure`` is 0 when the trial halted
  before the last checkpoint (``not_attempted`` counts as unsolved, memo §10.1);
- ``strict_solved[k]`` is 1 exactly when ``k`` lies in the reached prefix;
- a trajectory truncated by ``max_checkpoints`` has no trajectory-level
  outcome (S, C, reached, final solved, quality means): each is statused
  ``truncated``, because its last run checkpoint is not the problem's final
  checkpoint and K is not the problem's K. Per-checkpoint series and cost
  totals are kept; they describe what ran.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from raidar.analysis.outcomes import (
    FINAL_SOLVED,
    HEADLINE_SCORE,
    OUTCOME_METRICS,
    QUALITY_COMPONENTS,
    REACHED,
    SOLVE_RATE,
    SOLVED,
    STRICT_SOLVED,
    TRUNCATED,
    UNASSESSED,
    TrajectoryOutcome,
    Value,
)
from raidar.execution.models import CheckpointState, TrajectoryRecord
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus
from raidar.spec.policies import ScorePolicy, checkpoint_correctness

CORRECTNESS_REF = "correctness@1"
CODE_QUALITY_REF = "code-quality@1"
RESOURCE_USAGE_REF = "resource-usage@1"
PROCESS_COMPLIANCE_REF = "process-compliance@1"
ANALYSED_SCORERS = (CORRECTNESS_REF, CODE_QUALITY_REF, RESOURCE_USAGE_REF)
_COST_TOTALS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")
_CHECKPOINT_SOURCES = {
    "strict_pass_rate": CORRECTNESS_REF,
    "verbosity": CODE_QUALITY_REF,
    "erosion": CODE_QUALITY_REF,
    "architecture": CODE_QUALITY_REF,
    "cost_usd": RESOURCE_USAGE_REF,
}


@dataclass(frozen=True)
class Observed:
    """One scorer's result, looked up by (metric, subject)."""

    ref: str
    result: AssessResult | None

    def value(self, metric: str, subject: str = TRAJECTORY_SUBJECT) -> Value:
        if self.result is None:
            return Value.missing(UNASSESSED, f"no {self.ref} Assessment in the store")
        for observation in self.result.observations:
            if observation.metric == metric and observation.subject == subject:
                if observation.status is AssessStatus.OK and observation.value is not None:
                    return Value.ok(observation.value)
                return Value.missing(
                    observation.status.value, observation.reason or observation.status.value
                )
        status = self.result.status
        reason = self.result.reason or f"{self.ref} reported no {metric}[{subject}]"
        if status is AssessStatus.OK:
            status = AssessStatus.UNAVAILABLE
        return Value.missing(status.value, reason)


def infra_reasons(record: TrajectoryRecord) -> tuple[str, ...]:
    """Why a trajectory is infra-excluded: failed validity checks and infra checkpoints."""
    reasons = [f"validity:{check.name}" for check in record.validity.checks if not check.passed]
    for attempt in record.attempts:
        if attempt.checkpoint_state is CheckpointState.INFRA_ERROR:
            detail = ",".join(attempt.infra_reasons) or "infra_error"
            reasons.append(f"{attempt.step}:{detail}")
    if record.trial_exception:
        reasons.append(f"trial_exception:{record.trial_exception}")
    return tuple(dict.fromkeys(reasons))


def _final_solved(correctness: Observed, steps: tuple[str, ...], progression: str) -> Value:
    last = correctness.value(SOLVED, steps[-1])
    halted = (
        progression == "halt_on_failure"
        and last.status == AssessStatus.NOT_APPLICABLE.value
        and correctness.value(REACHED).measured
    )
    return Value.ok(0.0) if halted else last


def _solve_rate(correctness: Observed, checkpoints: int) -> Value:
    solved = correctness.value("solved_checkpoints")
    if solved.value is None:
        return solved
    return Value.ok(float(checkpoint_correctness(int(solved.value), checkpoints)))


def headline_score(policy: ScorePolicy, checkpoints: int, components: Mapping[str, Value]) -> Value:
    """The policy's S from statused components; any gap makes S ineligible (F6, F15).

    A component whose Assessment is missing makes S ``unassessed`` instead, so
    the report separates "not scored yet" from "not scorable".
    """
    unassessed = [f"{c}: {v.reason}" for c, v in components.items() if v.status == UNASSESSED]
    if unassessed:
        return Value.missing(UNASSESSED, f"{policy.ref}: {'; '.join(unassessed)}")
    values: dict[str, float] = {}
    ineligible: dict[str, list[str]] = {}
    for component, value in components.items():
        if value.value is not None:
            values[component] = value.value
        elif value.status != AssessStatus.NOT_APPLICABLE.value:
            ineligible[component] = [f"{value.status}: {value.reason}"]
    scored = policy.score(checkpoints=checkpoints, components=values, ineligible=ineligible)
    if scored.score is None:
        details = "; ".join(
            f"{reason.code.value}[{reason.component or '-'}]: {reason.detail}"
            for reason in scored.reasons
        )
        return Value.missing(AssessStatus.INELIGIBLE.value, f"{policy.ref}: {details}")
    return Value.ok(float(scored.score))


def _series(
    observed: Mapping[str, Observed], steps: tuple[str, ...], reached: Value
) -> dict[str, tuple[Value, ...]]:
    series = {SOLVED: tuple(observed[CORRECTNESS_REF].value(SOLVED, step) for step in steps)}
    series[STRICT_SOLVED] = tuple(
        Value.ok(1.0 if index < reached.value else 0.0) if reached.value is not None else reached
        for index in range(len(steps))
    )
    for series_id, ref in _CHECKPOINT_SOURCES.items():
        series[series_id] = tuple(observed[ref].value(series_id, step) for step in steps)
    return series


def _truncated(record: TrajectoryRecord) -> dict[str, Value]:
    reason = (
        f"run truncated to the first {record.truncated_to} checkpoint(s) (max_checkpoints); "
        "the trajectory-level outcome needs every checkpoint"
    )
    return {metric.id: Value.missing(TRUNCATED, reason) for metric in OUTCOME_METRICS}


def build_outcome(
    record: TrajectoryRecord,
    results: Mapping[str, AssessResult | None],
    *,
    policy: ScorePolicy,
) -> TrajectoryOutcome:
    """One trajectory's statused metrics and per-checkpoint series."""
    observed = {ref: Observed(ref, results.get(ref)) for ref in ANALYSED_SCORERS}
    correctness = observed[CORRECTNESS_REF]
    steps = record.trial.steps
    checkpoints = len(steps)
    quality = {
        name: observed[CODE_QUALITY_REF].value(f"problem_{name}") for name in QUALITY_COMPONENTS
    }
    solve_rate = _solve_rate(correctness, checkpoints)
    components = {"C": solve_rate} | {
        QUALITY_COMPONENTS[name]: value for name, value in quality.items()
    }
    reached = correctness.value(REACHED)
    metrics = {
        HEADLINE_SCORE: headline_score(policy, checkpoints, components),
        SOLVE_RATE: solve_rate,
        REACHED: reached,
        FINAL_SOLVED: _final_solved(correctness, steps, record.progression.mode),
        **quality,
        **{name: observed[RESOURCE_USAGE_REF].value(name) for name in _COST_TOTALS},
    }
    if record.truncated_to is not None:
        metrics |= _truncated(record)
    reasons = infra_reasons(record)
    return TrajectoryOutcome(
        trial_id=record.trial.trial_id,
        run_id=record.run_id,
        arm=record.trial.arm,
        block=record.trial.block,
        problem=record.trial.problem,
        steps=steps,
        infra_error=bool(reasons),
        infra_reasons=reasons,
        metrics=metrics,
        series=_series(observed, steps, reached),
    )
