"""Reduce one trial's record and Assessments to a ``TrialOutcome``.

Inputs are the job's ``TrialRecord`` (validity and identity) and the
stored ``AssessResult`` of each scorer (``None`` when the store has no
Assessment for this trial). Nothing is recomputed from raw run files:
correctness comes from ``correctness@1``, quality from ``code-quality@1`` (or ``@2``),
cost from ``resource-usage@1``, and the headline score from the experiment's
Metric applied to those components.

The score component C (``correctness``) is the policy's definition
(``spec.policies.problem_correctness``, the SCB fork's ``C_p``): the mean of
the per-checkpoint case pass rates, with an unreached checkpoint counted 0. It
is distinct from the primary outcome ``solve_rate`` (solved checkpoints / K).

Two derivations are analysis-owned:

- ``final_solved`` under ``halt_on_failure`` is 0 when the trial halted
  before the last checkpoint (``not_attempted`` counts as unsolved, memo §10.1);
- ``strict_solved[k]`` is 1 exactly when ``k`` lies in the reached prefix;
- the ``*_errata`` outcomes repeat ``solve_rate``, ``reached`` and
  ``final_solved`` over ``correctness@1``'s errata-applied metrics (the problem's
  pinned errata); they are labelled as such and never replace the raw ones;
- the ``gate.*`` outcomes (a gate-effort scorer, only when the lock declares
  one) are the scorer's trajectory metrics as stored: an arm without the commit
  gate reads ``not_applicable``; only the metrics that version reports are read;
- the ``process.*`` outcomes (``process.stall_rate``; ``process-compliance@3``,
  only when the lock declares it; ``@4`` adds ``process.compliance_rate`` and
  ``process.total_test_commands_not_run``) likewise: an arm without the stall wrapper
  reads ``not_applicable``;
- a trial truncated by ``max_checkpoints`` has no trial-level
  outcome (S, C, reached, final solved, quality means): each is statused
  ``truncated``, because its last run checkpoint is not the problem's final
  checkpoint and K is not the problem's K. Per-checkpoint series and cost
  totals are kept; they describe what ran.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from raidar.analysis.outcomes import (
    CORRECTNESS_METRIC,
    FINAL_SOLVED,
    FINAL_SOLVED_ERRATA,
    GATE_PREFIX,
    HEADLINE_SCORE,
    OUTCOME_METRICS,
    PROCESS_OUTCOME_SCORERS,
    PROCESS_PREFIX,
    QUALITY_COMPONENTS,
    REACHED,
    REACHED_ERRATA,
    SOLVE_RATE,
    SOLVE_RATE_ERRATA,
    SOLVED,
    STRICT_SOLVED,
    TRUNCATED,
    UNASSESSED,
    TrialOutcome,
    Value,
    gate_metrics_for,
    process_metrics_for,
)
from raidar.analysis.policy_inputs import ComponentInput, score_inputs
from raidar.execution.models import CheckpointState, TrialRecord
from raidar.scorers.code_quality_refs import CODE_QUALITY_REFS, code_quality_ref
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from raidar.spec.metrics import (
    CORRECTNESS,
    Metric,
    checkpoint_pass_rate,
    checkpoint_solve_rate,
    problem_correctness,
)

CORRECTNESS_REF = "correctness@1"
CODE_QUALITY_REF = "code-quality@1"
RESOURCE_USAGE_REF = "resource-usage@1"
# Process compliance the analysis reads, in preference order: ``@2`` decides
# only deterministically (the default since Jev was parked, 2026-10-04); the
# dormant ``@1`` stays readable for jobs whose lock declares it.
PROCESS_COMPLIANCE_REFS = (
    "process-compliance@4",
    "process-compliance@3",
    "process-compliance@2",
    "process-compliance@1",
)
ANALYSED_SCORERS = (CORRECTNESS_REF, CODE_QUALITY_REF, RESOURCE_USAGE_REF)
# The quality slot reads ``code-quality@2`` (offline G input) or ``@1``: whichever was declared.
QUALITY_SCORERS = CODE_QUALITY_REFS
# Read only when the lock declares one (its ``gate.*`` outcomes are then in every trial),
# in preference order: ``@2`` also reads a shadow gate's first-run outcomes, ``@4`` the
# tests check and the fix rounds' correctness.
GATE_EFFORT_REF = "gate-effort@1"
GATE_EFFORT_REFS = (
    "gate-effort@6",
    "gate-effort@5",
    "gate-effort@4",
    "gate-effort@3",
    "gate-effort@2",
    GATE_EFFORT_REF,
)
_COST_TOTALS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")
PASS_RATE = "strict_pass_rate"
_CHECKPOINT_SOURCES = {
    PASS_RATE: CORRECTNESS_REF,
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

    def observation(self, metric: str, subject: str = TRAJECTORY_SUBJECT) -> Observation | None:
        if self.result is None:
            return None
        return next(
            (
                item
                for item in self.result.observations
                if item.metric == metric and item.subject == subject
            ),
            None,
        )

    def value(self, metric: str, subject: str = TRAJECTORY_SUBJECT) -> Value:
        if self.result is None:
            return Value.missing(UNASSESSED, f"no {self.ref} Assessment in the store")
        observation = self.observation(metric, subject)
        if observation is not None:
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


def infra_reasons(record: TrialRecord) -> tuple[str, ...]:
    """Why a trial is infra-excluded: failed validity checks and infra checkpoints."""
    reasons = [f"validity:{check.name}" for check in record.validity.checks if not check.passed]
    for step_result in record.step_results:
        if step_result.checkpoint_state is CheckpointState.INFRA_ERROR:
            detail = ",".join(step_result.infra_reasons) or "infra_error"
            reasons.append(f"{step_result.step}:{detail}")
    if record.trial_exception:
        reasons.append(f"trial_exception:{record.trial_exception}")
    return tuple(dict.fromkeys(reasons))


ERRATA_SOLVED = "solved_errata"


def _final_solved(
    correctness: Observed,
    steps: tuple[str, ...],
    progression: str,
    *,
    solved: str = SOLVED,
    reached: str = REACHED,
) -> Value:
    last = correctness.value(solved, steps[-1])
    halted = (
        progression == "halt_on_failure"
        and last.status == AssessStatus.NOT_APPLICABLE.value
        and correctness.value(reached).measured
        and correctness.value(SOLVED, steps[-1]).status == AssessStatus.NOT_APPLICABLE.value
    )
    return Value.ok(0.0) if halted else last


def _solve_rate(
    correctness: Observed, checkpoints: int, metric: str = "solved_checkpoints"
) -> Value:
    solved = correctness.value(metric)
    if solved.value is None:
        return solved
    return Value.ok(float(checkpoint_solve_rate(int(solved.value), checkpoints)))


def _checkpoint_rate(correctness: Observed, step: str) -> Decimal | Value:
    """``C_p,k`` for one planned checkpoint, or the statused reason it has none.

    Unreached (``solved`` is ``not_applicable``) is the fork's unproduced
    checkpoint: 0. A reached checkpoint with no graded case is ineligible, as
    the fork's ``canonical_test_denominator_zero``.
    """
    if correctness.value(SOLVED, step).status == AssessStatus.NOT_APPLICABLE.value:
        return Decimal(0)
    observation = correctness.observation(PASS_RATE, step)
    if observation is not None and observation.status is AssessStatus.OK:
        if observation.numerator is not None and observation.denominator is not None:
            return checkpoint_pass_rate(int(observation.numerator), int(observation.denominator))
        if observation.value is not None:
            return Decimal(str(observation.value))
    rate = correctness.value(PASS_RATE, step)
    if rate.status == AssessStatus.NOT_APPLICABLE.value:
        return Value.missing(AssessStatus.INELIGIBLE.value, f"C: {step}: {rate.reason}")
    return Value.missing(rate.status, f"C: {step}: {rate.reason}")


def correctness_component(correctness: Observed, steps: tuple[str, ...]) -> Decimal | Value:
    """The policy's C (exact Decimal) over ``steps``, or the first statused gap."""
    rates: list[Decimal] = []
    for step in steps:
        rate = _checkpoint_rate(correctness, step)
        if isinstance(rate, Value):
            return rate
        rates.append(rate)
    return problem_correctness(rates)


def _policy_inputs(
    components: Mapping[str, Value], exact: Mapping[str, Decimal]
) -> dict[str, ComponentInput]:
    return {
        component: ComponentInput(value=exact[component])
        if component in exact
        else ComponentInput.from_value(value)
        for component, value in components.items()
    }


def headline_score(
    policy: Metric,
    checkpoints: int,
    components: Mapping[str, Value],
    exact: Mapping[str, Decimal] | None = None,
) -> Value:
    """The policy's S from statused components; a gap makes S ineligible (F6, F15).

    A component whose Assessment is missing makes S ``unassessed`` instead, so
    the report separates "not scored yet" from "not scorable". Under a policy
    with the ``zero_correctness`` rule, a measured C = 0 scores S = 0 whatever
    the quality components' status. ``exact`` carries Decimal values that
    replace the float in ``components`` (C, so S matches the fork exactly).
    """
    unassessed = [f"{c}: {v.reason}" for c, v in components.items() if v.status == UNASSESSED]
    not_scored = Value.missing(UNASSESSED, f"{policy.ref}: {'; '.join(unassessed)}")
    correctness = components.get(CORRECTNESS)
    if correctness is not None and correctness.status == UNASSESSED:
        return not_scored
    scored = score_inputs(policy, checkpoints, _policy_inputs(components, exact or {}))
    if scored.score is None and unassessed:
        return not_scored
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


def gate_effort_ref(declared: Iterable[str]) -> str | None:
    """The gate-effort version a lock declares (newest first), if any."""
    names = set(declared)
    return next((ref for ref in GATE_EFFORT_REFS if ref in names), None)


def gate_metrics(result: AssessResult | None, ref: str = GATE_EFFORT_REF) -> dict[str, Value]:
    """The ``gate.*`` outcomes from a trial's gate-effort trajectory observations.

    An arm without the gate reads ``not_applicable`` (the scorer's status), never 0;
    under ``gate-effort@2`` and ``@3`` a shadow gate's fix outcomes read ``not_applicable`` too.
    """
    observed = Observed(ref, result)
    return {
        metric.id: observed.value(metric.id.removeprefix(GATE_PREFIX))
        for metric in gate_metrics_for(ref)
    }


def process_metrics(
    result: AssessResult | None, ref: str = PROCESS_OUTCOME_SCORERS[-1]
) -> dict[str, Value]:
    """The ``process.*`` outcomes from a trial's ``process-compliance@3`` or ``@4`` result."""
    observed = Observed(ref, result)
    return {
        metric.id: observed.value(metric.id.removeprefix(PROCESS_PREFIX))
        for metric in process_metrics_for(ref)
    }


def _truncated(record: TrialRecord) -> dict[str, Value]:
    reason = (
        f"run truncated to the first {record.truncated_to} checkpoint(s) (max_checkpoints); "
        "the trial-level outcome needs every checkpoint"
    )
    return {metric.id: Value.missing(TRUNCATED, reason) for metric in OUTCOME_METRICS}


def _observed(results: Mapping[str, AssessResult | None]) -> dict[str, Observed]:
    """The analysed scorers' results; the quality slot reads the code-quality version used."""
    observed = {ref: Observed(ref, results.get(ref)) for ref in ANALYSED_SCORERS}
    quality_ref = code_quality_ref(ref for ref in results if results.get(ref) is not None)
    observed[CODE_QUALITY_REF] = Observed(quality_ref, results.get(quality_ref))
    return observed


def _effort_metrics(results: Mapping[str, AssessResult | None]) -> dict[str, Value]:
    """Gate and process outcomes, when the lock declares their scorers.

    Like cost totals, they describe what ran, truncated or not.
    """
    metrics: dict[str, Value] = {}
    gate_ref = gate_effort_ref(results)
    if gate_ref is not None:
        metrics |= gate_metrics(results[gate_ref], gate_ref)
    process_ref = next((ref for ref in PROCESS_OUTCOME_SCORERS if ref in results), None)
    if process_ref is not None:
        metrics |= process_metrics(results[process_ref], process_ref)
    return metrics


def build_outcome(
    record: TrialRecord,
    results: Mapping[str, AssessResult | None],
    *,
    policy: Metric,
) -> TrialOutcome:
    """One trial's statused metrics and per-checkpoint series."""
    observed = _observed(results)
    correctness = observed[CORRECTNESS_REF]
    steps = record.trial.steps
    checkpoints = len(steps)
    quality = {
        name: observed[CODE_QUALITY_REF].value(f"problem_{name}") for name in QUALITY_COMPONENTS
    }
    solve_rate = _solve_rate(correctness, checkpoints)
    exact = correctness_component(correctness, steps)
    c_value = exact if isinstance(exact, Value) else Value.ok(float(exact))
    components = {CORRECTNESS: c_value} | {
        QUALITY_COMPONENTS[name]: value for name, value in quality.items()
    }
    reached = correctness.value(REACHED)
    metrics = {
        HEADLINE_SCORE: headline_score(
            policy,
            checkpoints,
            components,
            exact={} if isinstance(exact, Value) else {CORRECTNESS: exact},
        ),
        CORRECTNESS_METRIC: c_value,
        SOLVE_RATE: solve_rate,
        REACHED: reached,
        FINAL_SOLVED: _final_solved(correctness, steps, record.progression.mode),
        SOLVE_RATE_ERRATA: _solve_rate(correctness, checkpoints, "solved_checkpoints_errata"),
        REACHED_ERRATA: correctness.value(REACHED_ERRATA),
        FINAL_SOLVED_ERRATA: _final_solved(
            correctness,
            steps,
            record.progression.mode,
            solved=ERRATA_SOLVED,
            reached=REACHED_ERRATA,
        ),
        **quality,
        **{name: observed[RESOURCE_USAGE_REF].value(name) for name in _COST_TOTALS},
    }
    metrics |= _effort_metrics(results)
    if record.truncated_to is not None:
        metrics |= _truncated(record)
    reasons = infra_reasons(record)
    return TrialOutcome(
        trial_id=record.trial.trial_id,
        job_id=record.job_id,
        arm=record.trial.arm,
        attempt=record.trial.attempt,
        problem=record.trial.problem,
        steps=steps,
        infra_error=bool(reasons),
        infra_reasons=reasons,
        metrics=metrics,
        series=_series(observed, steps, reached),
    )
