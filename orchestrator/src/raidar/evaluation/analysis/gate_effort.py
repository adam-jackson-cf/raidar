"""Gate-effort (and process) outcomes in the analysis report, when the lock declares the scorer.

A commit gate's effort (rounds to clean, first-pass clean, unresolved and
gate-error rates, blocking findings by rule family, fix-session tokens, cost
and time) is a quality signal of its own. It is reported two ways:

- **per arm, descriptive** (``ArmSummary.gate_effort``): over the arm's valid
  trials, the measured values' count, mean, SD and interval, plus the gaps by
  status. An arm without the gate has no measured value (every trial
  ``not_applicable``), so its row says so instead of reading as zero;
- **paired** (``Comparison.gate_effects``): the same paired engine as every
  other outcome, for each gate metric that both arms of the comparison measured
  in at least one valid trial. A baseline without the gate therefore yields no
  paired gate effect, and the comparison says why (``gate_note``).

The same two views serve the ``process.*`` outcomes (``process-compliance``:
the stall rate) when the lock declares that scorer: an arm without the stall
wrapper has no value.

Nothing here gets a verdict that the primary outcome does not already have:
gate effects carry the usual paired verdict for the metric's declared
direction, and ranking rows show the arm means beside the ranked metric.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import Field

from raidar.evaluation.analysis.effects import (
    Effect,
    IntervalOut,
    Settings,
    Slot,
    aggregate_effect,
    interval_out,
    problem_effect,
)
from raidar.evaluation.analysis.outcomes import AnalysisModel, TrialOutcome
from raidar.evaluation.analysis.stats import seed_for, summarise
from raidar.scoring.catalog.outcomes import GATE_METRICS, MetricDef

NO_GATE_NOTE = (
    "no paired gate effect: the {arm} arm measured no gate-effort value (it ran without "
    "the commit gate); see the arms' gate_effort descriptives"
)
NO_PROCESS_NOTE = (
    "no paired process effect: the {arm} arm measured no process outcome (it ran without "
    "the stall wrapper); see the arms' process_outcomes descriptives"
)


class GateDescriptive(AnalysisModel):
    """One arm's values of one gate metric over its valid trials."""

    n: int = Field(ge=0)
    mean: float | None
    sd: float | None
    interval: IntervalOut | None
    gaps: dict[str, int] = Field(default_factory=dict)
    """Valid trials without a value, by status (``not_applicable``: no gate in the trial)."""


def _gaps(valid: Sequence[TrialOutcome], metric: str) -> dict[str, int]:
    gaps: dict[str, int] = {}
    for outcome in valid:
        value = outcome.metric(metric)
        if not value.measured:
            gaps[value.status] = gaps.get(value.status, 0) + 1
    return dict(sorted(gaps.items()))


def arm_gate_effort(
    arm: str,
    valid: Sequence[TrialOutcome],
    settings: Settings,
    metrics: Sequence[MetricDef] = GATE_METRICS,
) -> dict[str, GateDescriptive]:
    """Every gate (or process) metric's descriptive summary over one arm's valid trials."""
    described = {}
    for metric in metrics:
        values = [
            value.value
            for outcome in valid
            if (value := outcome.metric(metric.id)).measured and value.value is not None
        ]
        summary = summarise(
            values,
            seed=seed_for(settings.seed, f"gate:{arm}:{metric.id}"),
            bootstrap_samples=settings.bootstrap_samples,
            confidence=1.0 - settings.alpha,
        )
        described[metric.id] = GateDescriptive(
            n=summary.n,
            mean=summary.mean,
            sd=summary.sd,
            interval=interval_out(summary.interval),
            gaps=_gaps(valid, metric.id),
        )
    return described


def _measured_in(outcomes: Sequence[TrialOutcome | None], metric: str) -> bool:
    return any(
        outcome is not None and not outcome.infra_error and outcome.metric(metric).measured
        for outcome in outcomes
    )


def _both_measured(slots: Sequence[Slot], metric: MetricDef) -> bool:
    return _measured_in([slot.baseline for slot in slots], metric.id) and _measured_in(
        [slot.treatment for slot in slots], metric.id
    )


def _metric_effects(
    metric: MetricDef, slots: Mapping[str, Sequence[Slot]], settings: Settings, label: str
) -> list[Effect]:
    """One gate metric's per-problem effects (problems both arms measured) and aggregate."""
    per_problem = [
        problem_effect(metric, problem, problem_slots, settings, label=label)
        for problem, problem_slots in slots.items()
        if _both_measured(problem_slots, metric)
    ]
    if len(per_problem) > 1:
        return [*per_problem, aggregate_effect(metric, per_problem, settings)]
    return per_problem


def gate_effects(
    slots: Mapping[str, Sequence[Slot]],
    settings: Settings,
    *,
    label: str,
    metrics: Sequence[MetricDef] = GATE_METRICS,
    note: str = NO_GATE_NOTE,
) -> tuple[tuple[Effect, ...], str | None]:
    """Paired effects both arms measured, and a note when none could be paired."""
    every = [slot for problem_slots in slots.values() for slot in problem_slots]
    effects = [
        effect
        for metric in metrics
        if _both_measured(every, metric)
        for effect in _metric_effects(metric, slots, settings, label)
    ]
    if effects:
        return tuple(effects), None
    baseline_measured = _measured_in([slot.baseline for slot in every], metrics[0].id)
    arm = "treatment" if baseline_measured else "baseline"
    return (), note.format(arm=arm)


def gate_means(described: Mapping[str, GateDescriptive] | None) -> dict[str, float | None] | None:
    """Arm means of every gate metric (the ranking view's gate columns)."""
    if described is None:
        return None
    return {metric: item.mean for metric, item in described.items()}
