"""Paired effects: treatment minus baseline per attempt, per problem (F10, memo §2.3).

A *slot* is one (problem, attempt) the plan scheduled for both arms. Each slot
either yields a pair ``D = Y[treatment] − Y[baseline]`` or is excluded, and
every exclusion is counted by cause:

- ``not_run``: an arm's trial is absent from the analysed runs;
- ``infra_error``: an arm's trial failed the execution-validity gate or
  has an ``infra_error`` checkpoint;
- ``unassessed``: the Assessment store has no result for the needed scorer;
- ``ineligible``: the value exists as a statused gap (``ineligible``,
  ``unavailable``, ...), tallied per status.

Verdicts (memo §2.3), in precedence order:

1. ``insufficient``: fewer pairs than the minimum, or the excluded share of
   planned slots above the limit (memo §2.3: both are pre-registered, pinned
   by the lock's ``analysis`` section; ``Settings.origin`` says whether a value
   came from the lock, a command-line override or the default);
2. ``inconclusive``: the ``1 - alpha`` CI (95% at the default alpha) spans 0
   (binary outcomes: exact McNemar p > alpha);
3. ``improved`` / ``worse``: the CI excludes 0, read through the metric's
   direction.

Cost is estimated on the same pairs and shown beside every effect; it never
gets a verdict of its own. The J2 projection ``n = 7.84·(SD/δ)²`` uses the
effect's pilot SD. The equal-weight aggregate projects the pairs per problem
that give its equal-weight mean the same power, and costs one more attempt
across every problem.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import Field

from raidar.design.experiment.schema import DEFAULT_ALPHA as DEFAULT_ALPHA
from raidar.design.experiment.schema import (
    DEFAULT_MAX_EXCLUSION_FRACTION as DEFAULT_MAX_EXCLUSION_FRACTION,
)
from raidar.design.experiment.schema import DEFAULT_MIN_PAIRS as DEFAULT_MIN_PAIRS
from raidar.evaluation.analysis.outcomes import (
    TOTAL_COST,
    UNASSESSED,
    AnalysisModel,
    Kind,
    MetricDef,
    TrialOutcome,
    Value,
)
from raidar.evaluation.analysis.stats import (
    BOOTSTRAP_SAMPLES,
    CONFIDENCE,
    Interval,
    IntervalMethod,
    Summary,
    confidence_label,
    mcnemar_exact,
    mean,
    pairs_for_power,
    seed_for,
    summarise,
    t_interval,
)
from raidar.scoring.framework.manifest import Direction
from raidar.scoring.framework.result import AssessStatus

AGGREGATE = "*"


class Verdict(StrEnum):
    IMPROVED = "improved"
    WORSE = "worse"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Settings:
    """Verdict thresholds and resampling settings; all of them are echoed into the report.

    The thresholds are pre-registered (memo §2.3): ``raidar.evaluation.analysis.thresholds``
    reads them from the lock's ``analysis`` section (``pinned_by_lock``) and
    names each one a command-line option replaced (``overridden``). ``source``
    summarises that; the report also lists every value that differs from the
    documented default, so a post-hoc change is visible.
    """

    min_pairs: int = DEFAULT_MIN_PAIRS
    max_exclusion_fraction: float = DEFAULT_MAX_EXCLUSION_FRACTION
    alpha: float = DEFAULT_ALPHA
    bootstrap_samples: int = BOOTSTRAP_SAMPLES
    seed: int = 0
    min_effects: Mapping[str, float] = field(default_factory=dict)
    source: str = "caller"
    pinned_by_lock: bool = False
    overridden: tuple[str, ...] = ()

    def origin(self, name: str) -> str:
        """Where threshold ``name`` came from, for verdict reasons."""
        if name in self.overridden:
            return "command-line override of the lock"
        if self.pinned_by_lock:
            return "pinned by the lock"
        return "not pinned by a lock"

    def changed_from_defaults(self) -> tuple[str, ...]:
        defaults = {
            "min_pairs": DEFAULT_MIN_PAIRS,
            "max_exclusion_fraction": DEFAULT_MAX_EXCLUSION_FRACTION,
            "alpha": DEFAULT_ALPHA,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
        }
        changed = [name for name, value in defaults.items() if getattr(self, name) != value]
        changed += [f"min_effect.{name}" for name in sorted(self.min_effects)]
        return tuple(changed)

    def __post_init__(self) -> None:
        if self.min_pairs < 2:
            raise ValueError("min_pairs must be at least 2 (a CI needs two pairs)")
        if not 0.0 <= self.max_exclusion_fraction <= 1.0:
            raise ValueError("max_exclusion_fraction must be in [0, 1]")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        if self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        bad = {name: value for name, value in self.min_effects.items() if value <= 0}
        if bad:
            raise ValueError(f"effects of interest must be positive: {bad}")


class IntervalOut(AnalysisModel):
    low: float
    high: float
    method: IntervalMethod
    confidence: float = CONFIDENCE


def interval_out(interval: Interval | None) -> IntervalOut | None:
    if interval is None:
        return None
    return IntervalOut(
        low=interval.low, high=interval.high, method=interval.method, confidence=interval.confidence
    )


class Exclusions(AnalysisModel):
    planned: int = Field(ge=0)
    paired: int = Field(ge=0)
    not_run: int = 0
    infra_error: int = 0
    unassessed: int = 0
    ineligible: int = 0
    ineligible_statuses: dict[str, int] = Field(default_factory=dict)
    not_applicable: int = 0
    """Slots where the metric is undefined in both arms (e.g. R/G at K=1): not planned."""

    @property
    def excluded(self) -> int:
        return self.planned - self.paired

    @property
    def fraction(self) -> float:
        return self.excluded / self.planned if self.planned else 0.0


class Pair(AnalysisModel):
    problem: str
    attempt: int
    baseline_trial: str
    treatment_trial: str
    baseline: float
    treatment: float
    delta: float


class McNemar(AnalysisModel):
    both_solved: int
    baseline_only: int
    treatment_only: int
    neither: int
    p_value: float


class CostBeside(AnalysisModel):
    """Resource usage on the effect's own pairs (descriptive; ``resource-usage@1``)."""

    metric: str = TOTAL_COST
    n: int
    unavailable: int
    baseline_mean: float | None
    treatment_mean: float | None
    delta_mean: float | None
    delta_ci: IntervalOut | None


class SampleSize(AnalysisModel):
    """J2: pairs for 80% power at alpha 0.05 from the pilot SD.

    ``recommended_pairs`` counts attempts per problem; ``per_pair_cost_usd`` is
    one more attempt (both arms, every problem the effect covers).
    """

    basis: str = "problem"
    delta: float
    delta_source: str
    pilot_pairs: int
    pilot_sd: float | None
    standardized_effect: float | None
    recommended_pairs: int | None
    additional_pairs: int | None
    per_pair_cost_usd: float | None
    projected_cost_usd: float | None
    reason: str | None = None


class Effect(AnalysisModel):
    metric: str
    label: str
    view: str
    direction: Direction
    kind: Kind
    problem: str
    n_pairs: int
    baseline_mean: float | None
    treatment_mean: float | None
    mean_delta: float | None
    sd_delta: float | None
    ci: IntervalOut | None
    verdict: Verdict
    verdict_reason: str
    exclusions: Exclusions
    mcnemar: McNemar | None = None
    cost: CostBeside
    sample_size: SampleSize | None = None
    pairs: tuple[Pair, ...] = ()


@dataclass(frozen=True)
class Slot:
    problem: str
    attempt: int
    baseline: TrialOutcome | None
    treatment: TrialOutcome | None


ValueOf = Callable[[TrialOutcome], Value]


@dataclass
class _Tally:
    planned: int = 0
    not_run: int = 0
    infra_error: int = 0
    unassessed: int = 0
    not_applicable: int = 0
    statuses: dict[str, int] = field(default_factory=dict)

    def exclusions(self, paired: int) -> Exclusions:
        return Exclusions(
            planned=self.planned,
            paired=paired,
            not_run=self.not_run,
            infra_error=self.infra_error,
            unassessed=self.unassessed,
            ineligible=sum(self.statuses.values()),
            ineligible_statuses=dict(sorted(self.statuses.items())),
            not_applicable=self.not_applicable,
        )


NOT_APPLICABLE = AssessStatus.NOT_APPLICABLE.value


def metric_applies(effect: Effect) -> bool:
    """False when the metric is undefined for every slot of the problem (R/G at K=1)."""
    return not (effect.exclusions.planned == 0 and effect.exclusions.not_applicable > 0)


def _gap_excluded(left: Value, right: Value, tally: _Tally) -> bool:
    """Tally a slot with a missing value; True when it was excluded (or never planned)."""
    if left.value is not None and right.value is not None:
        return False
    if {left.status, right.status} == {NOT_APPLICABLE}:
        # Undefined for this problem in both arms: no pair was ever planned.
        tally.planned -= 1
        tally.not_applicable += 1
        return True
    gap = left if left.value is None else right
    if gap.status == UNASSESSED:
        tally.unassessed += 1
    else:
        tally.statuses[gap.status] = tally.statuses.get(gap.status, 0) + 1
    return True


def _pair_or_exclude(slot: Slot, value_of: ValueOf, tally: _Tally) -> Pair | None:
    tally.planned += 1
    baseline, treatment = slot.baseline, slot.treatment
    if baseline is None or treatment is None:
        tally.not_run += 1
        return None
    if baseline.infra_error or treatment.infra_error:
        tally.infra_error += 1
        return None
    left, right = value_of(baseline), value_of(treatment)
    if _gap_excluded(left, right, tally) or left.value is None or right.value is None:
        return None
    return Pair(
        problem=slot.problem,
        attempt=slot.attempt,
        baseline_trial=baseline.trial_id,
        treatment_trial=treatment.trial_id,
        baseline=left.value,
        treatment=right.value,
        delta=right.value - left.value,
    )


def collect_pairs(slots: Sequence[Slot], value_of: ValueOf) -> tuple[tuple[Pair, ...], Exclusions]:
    tally = _Tally()
    pairs = tuple(
        pair for slot in slots if (pair := _pair_or_exclude(slot, value_of, tally)) is not None
    )
    return pairs, tally.exclusions(len(pairs))


def mcnemar_table(pairs: Sequence[Pair]) -> McNemar:
    cells = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    for pair in pairs:
        cells[(pair.baseline >= 0.5, pair.treatment >= 0.5)] += 1
    baseline_only, treatment_only = cells[(True, False)], cells[(False, True)]
    return McNemar(
        both_solved=cells[(True, True)],
        baseline_only=baseline_only,
        treatment_only=treatment_only,
        neither=cells[(False, False)],
        p_value=mcnemar_exact(baseline_only, treatment_only),
    )


def _better(direction: Direction, delta: float) -> bool:
    return delta > 0 if direction is Direction.HIGHER_BETTER else delta < 0


def _directed(direction: Direction, delta: float, reason: str) -> tuple[Verdict, str]:
    """A significant ``delta`` read against the metric's direction; a neutral metric has none."""
    if direction is Direction.NEUTRAL:
        return Verdict.INCONCLUSIVE, f"{reason}, but the metric has no better direction"
    return (Verdict.IMPROVED if _better(direction, delta) else Verdict.WORSE), reason


def decide(
    metric: MetricDef,
    summary: Summary,
    exclusions: Exclusions,
    settings: Settings,
    mcnemar: McNemar | None = None,
) -> tuple[Verdict, str]:
    """The verdict and why, in the documented precedence."""
    if summary.n < settings.min_pairs:
        return Verdict.INSUFFICIENT, (
            f"{summary.n} pairs < minimum {settings.min_pairs} ({settings.origin('min_pairs')})"
        )
    if exclusions.fraction > settings.max_exclusion_fraction:
        return Verdict.INSUFFICIENT, (
            f"{exclusions.excluded}/{exclusions.planned} planned pairs excluded "
            f"> limit {settings.max_exclusion_fraction:.0%}"
        )
    if mcnemar is not None:
        if mcnemar.p_value > settings.alpha:
            return Verdict.INCONCLUSIVE, f"exact McNemar p={mcnemar.p_value:.4g} > {settings.alpha}"
        delta = float(mcnemar.treatment_only - mcnemar.baseline_only)
        reason = f"exact McNemar p={mcnemar.p_value:.4g} <= {settings.alpha}"
    else:
        interval = summary.interval
        if interval is None or summary.mean is None:
            return Verdict.INCONCLUSIVE, "no interval"
        level = confidence_label(interval.confidence)
        if interval.contains_zero():
            return Verdict.INCONCLUSIVE, (
                f"{level} CI [{interval.low:.4g}, {interval.high:.4g}] spans 0"
            )
        delta = summary.mean
        reason = f"{level} CI [{interval.low:.4g}, {interval.high:.4g}] excludes 0"
    return _directed(metric.direction, delta, reason)


def mean_or_none(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def mean_if_complete(values: Sequence[float | None]) -> float | None:
    """The mean when every value is present, else None (a partial mean would mislead)."""
    present = [value for value in values if value is not None]
    return mean(present) if present and len(present) == len(values) else None


def cost_beside(
    pairs: Sequence[Pair], slots: Sequence[Slot], settings: Settings, *, label: str
) -> CostBeside:
    """Paired ``total_cost_usd`` on the attempts behind ``pairs``."""
    attempts = {pair.attempt for pair in pairs}
    cost_pairs, exclusions = collect_pairs(
        [slot for slot in slots if slot.attempt in attempts],
        lambda outcome: outcome.metric(TOTAL_COST),
    )
    summary = summarise(
        [pair.delta for pair in cost_pairs],
        seed=seed_for(settings.seed, f"cost:{label}"),
        bootstrap_samples=settings.bootstrap_samples,
        confidence=1.0 - settings.alpha,
    )
    return CostBeside(
        n=len(cost_pairs),
        unavailable=exclusions.excluded,
        baseline_mean=mean_or_none([pair.baseline for pair in cost_pairs]),
        treatment_mean=mean_or_none([pair.treatment for pair in cost_pairs]),
        delta_mean=summary.mean,
        delta_ci=interval_out(summary.interval),
    )


def _per_pair_cost(cost: CostBeside) -> float | None:
    """One more pair costs a baseline and a treatment trial."""
    if cost.baseline_mean is None or cost.treatment_mean is None:
        return None
    return cost.baseline_mean + cost.treatment_mean


def _no_projection(sd: float | None) -> str | None:
    if sd is None:
        return "no projection: fewer than 2 pairs"
    if sd == 0.0:
        return "no projection: pilot SD is 0 (no spread)"
    return None


def sample_size(
    metric: MetricDef,
    summary: Summary,
    per_pair: float | None,
    settings: Settings,
    *,
    basis: str = "problem",
) -> SampleSize:
    """J2: ``n = 7.84·(SD/δ)²`` from this pilot's SD, with its projected cost."""
    configured = metric.id in settings.min_effects
    delta = settings.min_effects[metric.id] if configured else metric.min_effect
    reason = _no_projection(summary.sd)
    sd = summary.sd if reason is None else None
    recommended = pairs_for_power(sd, delta) if sd is not None else None
    return SampleSize(
        basis=basis,
        delta=delta,
        delta_source="configured" if configured else "default",
        pilot_pairs=summary.n,
        pilot_sd=summary.sd,
        standardized_effect=delta / sd if sd is not None else None,
        recommended_pairs=recommended,
        additional_pairs=max(0, recommended - summary.n) if recommended is not None else None,
        per_pair_cost_usd=per_pair,
        projected_cost_usd=(
            recommended * per_pair if recommended is not None and per_pair is not None else None
        ),
        reason=reason,
    )


def problem_effect(
    metric: MetricDef,
    problem: str,
    slots: Sequence[Slot],
    settings: Settings,
    *,
    label: str,
) -> Effect:
    """The paired effect of one metric on one problem's slots."""
    pairs, exclusions = collect_pairs(slots, lambda outcome: outcome.metric(metric.id))
    summary = summarise(
        [pair.delta for pair in pairs],
        seed=seed_for(settings.seed, f"{label}:{problem}:{metric.id}"),
        bootstrap_samples=settings.bootstrap_samples,
        confidence=1.0 - settings.alpha,
    )
    mcnemar = mcnemar_table(pairs) if metric.kind is Kind.BINARY else None
    verdict, reason = decide(metric, summary, exclusions, settings, mcnemar)
    cost = cost_beside(pairs, slots, settings, label=f"{label}:{problem}:{metric.id}")
    return Effect(
        metric=metric.id,
        label=metric.label,
        view=metric.view.value,
        direction=metric.direction,
        kind=metric.kind,
        problem=problem,
        n_pairs=len(pairs),
        baseline_mean=mean_or_none([p.baseline for p in pairs]),
        treatment_mean=mean_or_none([p.treatment for p in pairs]),
        mean_delta=summary.mean,
        sd_delta=summary.sd,
        ci=interval_out(summary.interval),
        verdict=verdict,
        verdict_reason=reason,
        exclusions=exclusions,
        mcnemar=mcnemar,
        cost=cost,
        sample_size=sample_size(metric, summary, _per_pair_cost(cost), settings),
        pairs=pairs,
    )


def _summed_exclusions(effects: Sequence[Effect]) -> Exclusions:
    statuses: dict[str, int] = {}
    for effect in effects:
        for status, count in effect.exclusions.ineligible_statuses.items():
            statuses[status] = statuses.get(status, 0) + count
    return Exclusions(
        planned=sum(e.exclusions.planned for e in effects),
        paired=sum(e.exclusions.paired for e in effects),
        not_run=sum(e.exclusions.not_run for e in effects),
        infra_error=sum(e.exclusions.infra_error for e in effects),
        unassessed=sum(e.exclusions.unassessed for e in effects),
        ineligible=sum(e.exclusions.ineligible for e in effects),
        ineligible_statuses=dict(sorted(statuses.items())),
        not_applicable=sum(e.exclusions.not_applicable for e in effects),
    )


def _equal_weight_summary(effects: Sequence[Effect], confidence: float) -> Summary:
    """Equal-weight mean of problem means; SE combines problem SEs (clustered by problem)."""
    n = min(effect.n_pairs for effect in effects)
    if n < 2 or any(e.mean_delta is None or e.sd_delta is None for e in effects):
        return Summary(n=n, mean=None, sd=None, interval=None)
    means = [e.mean_delta for e in effects if e.mean_delta is not None]
    variances = [(e.sd_delta or 0.0) ** 2 / e.n_pairs for e in effects]
    centre = mean(means)
    standard_error = math.sqrt(math.fsum(variances)) / len(effects)
    interval = t_interval(
        centre,
        standard_error,
        n - 1,
        IntervalMethod.PROBLEM_EQUAL_WEIGHT_T,
        confidence=confidence,
    )
    return Summary(n=n, mean=centre, sd=None, interval=interval)


def _effective_sd(effects: Sequence[Effect]) -> float | None:
    """``sqrt(Σ SD_p²) / P``, the per-problem SD with the equal-weight mean's power."""
    sds = [effect.sd_delta for effect in effects]
    if any(sd is None for sd in sds):
        return None
    return math.sqrt(math.fsum((sd or 0.0) ** 2 for sd in sds)) / len(effects)


def _attempt_cost(effects: Sequence[Effect]) -> float | None:
    """One more attempt: a pair on every problem, when every problem's cost is known."""
    costs = [_per_pair_cost(effect.cost) for effect in effects]
    if any(cost is None for cost in costs):
        return None
    return math.fsum(cost or 0.0 for cost in costs)


def _aggregate_sample_size(
    metric: MetricDef, effects: Sequence[Effect], settings: Settings
) -> SampleSize:
    """Pairs per problem for the equal-weight mean, whose variance is Σ SD_p² / (P² n)."""
    summary = Summary(
        n=min(effect.n_pairs for effect in effects),
        mean=None,
        sd=_effective_sd(effects),
        interval=None,
    )
    return sample_size(
        metric,
        summary,
        _attempt_cost(effects),
        settings,
        basis=f"equal-weight aggregate of {len(effects)} problems (pairs per problem)",
    )


def _pooled_mcnemar(effects: Sequence[Effect]) -> McNemar | None:
    tables = [effect.mcnemar for effect in effects if effect.mcnemar is not None]
    if not tables:
        return None
    baseline_only = sum(t.baseline_only for t in tables)
    treatment_only = sum(t.treatment_only for t in tables)
    return McNemar(
        both_solved=sum(t.both_solved for t in tables),
        baseline_only=baseline_only,
        treatment_only=treatment_only,
        neither=sum(t.neither for t in tables),
        p_value=mcnemar_exact(baseline_only, treatment_only),
    )


def _aggregate_cost(costs: Sequence[CostBeside]) -> CostBeside:
    return CostBeside(
        n=sum(cost.n for cost in costs),
        unavailable=sum(cost.unavailable for cost in costs),
        baseline_mean=mean_if_complete([cost.baseline_mean for cost in costs]),
        treatment_mean=mean_if_complete([cost.treatment_mean for cost in costs]),
        delta_mean=mean_if_complete([cost.delta_mean for cost in costs]),
        delta_ci=None,
    )


def aggregate_effect(metric: MetricDef, effects: Sequence[Effect], settings: Settings) -> Effect:
    """Across problems: estimate within each problem, then weight problems equally.

    ``n_pairs`` is the smallest problem's pair count, so ``insufficient``
    applies when any problem is short; exclusions are summed.
    """
    summary = _equal_weight_summary(effects, 1.0 - settings.alpha)
    exclusions = _summed_exclusions(effects)
    mcnemar = _pooled_mcnemar(effects) if metric.kind is Kind.BINARY else None
    verdict, reason = decide(metric, summary, exclusions, settings, mcnemar)
    return Effect(
        metric=metric.id,
        label=metric.label,
        view=metric.view.value,
        direction=metric.direction,
        kind=metric.kind,
        problem=AGGREGATE,
        n_pairs=summary.n,
        baseline_mean=mean_if_complete([effect.baseline_mean for effect in effects]),
        treatment_mean=mean_if_complete([effect.treatment_mean for effect in effects]),
        mean_delta=summary.mean,
        sd_delta=None,
        ci=interval_out(summary.interval),
        verdict=verdict,
        verdict_reason=reason,
        exclusions=exclusions,
        mcnemar=mcnemar,
        cost=_aggregate_cost([effect.cost for effect in effects]),
        sample_size=_aggregate_sample_size(metric, effects, settings),
    )
