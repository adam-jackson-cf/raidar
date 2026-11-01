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

1. ``insufficient``: fewer pairs than the minimum, the excluded share of planned slots above
   the limit, or per-arm exclusion rates (each arm's excluded slots over planned slots, by
   cause) differing by more than the lock's pre-registered ``max_exclusion_difference``, or
   that difference being unpinned (a non-significant difference never establishes acceptable
   attrition). ``Settings.origin`` says whether a value came from the lock, a command-line
   override or the default;
2. ``inconclusive``: the exact test's p-value exceeds alpha. The test follows the outcome's
   *declared* type, never its observed values: McNemar for binary outcomes, an exact
   sign-flip permutation test on the paired deltas otherwise. Aggregates test across problems
   (see ``aggregate_effect``). The ``1 - alpha`` interval only describes the estimate. There is
   no zero-spread rule: five one-way binary pairs give p = 0.0625, six give 0.03125;
3. ``improved`` / ``worse``: p <= alpha, read through the metric's direction.

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
from fractions import Fraction
from typing import Any

from pydantic import Field

from raidar.design.experiment.schema import DEFAULT_ALPHA as DEFAULT_ALPHA
from raidar.design.experiment.schema import (
    DEFAULT_MAX_EXCLUSION_FRACTION as DEFAULT_MAX_EXCLUSION_FRACTION,
)
from raidar.design.experiment.schema import DEFAULT_MIN_PAIRS as DEFAULT_MIN_PAIRS
from raidar.evaluation.analysis.outcomes import UNASSESSED, AnalysisModel, TrialOutcome, Value
from raidar.evaluation.analysis.stats import (
    BOOTSTRAP_SAMPLES,
    CONFIDENCE,
    POWER_CONSTANT,
    SIGN_FLIP_STATE_LIMIT,
    Interval,
    IntervalMethod,
    Summary,
    mcnemar_exact,
    mean,
    pairs_for_power,
    seed_for,
    sign_flip_exact,
    summarise,
    t_interval,
)
from raidar.scoring.catalog.outcomes import TOTAL_COST, Kind, MetricDef
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
    max_exclusion_difference: float | None = None
    """Pre-registered largest difference in per-arm exclusion rates; None: unpinned, so no
    comparative verdict is possible."""
    outcome_types: Mapping[str, Any] | None = None
    """The lock's ``analysis.outcome_types`` declarations; None: undeclared, so no comparative
    verdict is possible."""
    sign_flip_state_limit: int = SIGN_FLIP_STATE_LIMIT
    """Execution safety limit on the exact sign-flip enumeration (distinct partial sums); above
    it the verdict is refused as not computable. It never changes a computed p-value."""
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
        self._validate_counts()
        self._validate_fractions()
        bad = {name: value for name, value in self.min_effects.items() if value <= 0}
        if bad:
            raise ValueError(f"effects of interest must be positive: {bad}")

    def _validate_counts(self) -> None:
        if self.min_pairs < 2:
            raise ValueError("min_pairs must be at least 2 (a CI needs two pairs)")
        if self.sign_flip_state_limit < 1:
            raise ValueError("sign_flip_state_limit must be at least 1")
        if self.bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")

    def _validate_fractions(self) -> None:
        if not 0.0 <= self.max_exclusion_fraction <= 1.0:
            raise ValueError("max_exclusion_fraction must be in [0, 1]")
        difference = self.max_exclusion_difference
        if difference is not None and not 0.0 <= difference <= 1.0:
            raise ValueError("max_exclusion_difference must be in [0, 1]")
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")


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


class ArmExclusions(AnalysisModel):
    arm: str
    planned: int = Field(ge=0)
    excluded: int = Field(ge=0)
    """Planned slots lost to this arm's own trial."""
    causes: dict[str, int] = Field(default_factory=dict)
    """``not_run``, ``infra_error:<recorded reason>`` (each validity check, infra checkpoint or
    exception), ``unassessed`` or ``ineligible:<status>`` -> slots. A trial with several
    recorded reasons counts under each, so the causes can sum to more than ``excluded``."""

    @property
    def rate(self) -> float:
        return self.excluded / self.planned if self.planned else 0.0

    def plus(self, other: ArmExclusions) -> ArmExclusions:
        causes = dict(self.causes)
        for cause, count in other.causes.items():
            causes[cause] = causes.get(cause, 0) + count
        return ArmExclusions(
            arm=self.arm,
            planned=self.planned + other.planned,
            excluded=self.excluded + other.excluded,
            causes=dict(sorted(causes.items())),
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
    arms: tuple[ArmExclusions, ...] = ()
    """Per arm, per cause: slots excluded because of that arm's trial (a slot excluded for both
    arms is counted in both)."""

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


class ExactTestName(StrEnum):
    MCNEMAR = "mcnemar-exact"
    SIGN_FLIP = "sign-flip-exact"


class ExactTest(AnalysisModel):
    """The verdict's test, chosen from the outcome's declared type (binary: McNemar)."""

    name: ExactTestName
    p_value: float | None
    """None when the exact enumeration is not computable (``note`` says why)."""
    statistic: float
    """Signed: treatment-only minus baseline-only discordant pairs (McNemar) or the
    (weighted) sum of deltas (sign-flip); it orients an improved/worse verdict."""
    informative: int
    """Discordant pairs (McNemar) or non-zero deltas (sign-flip); zero ones carry no weight."""
    note: str | None = None

    @property
    def label(self) -> str:
        return "exact McNemar" if self.name is ExactTestName.MCNEMAR else "exact sign-flip"


class McNemar(AnalysisModel):
    both_solved: int
    baseline_only: int
    treatment_only: int
    neither: int
    p_value: float


class CostBeside(AnalysisModel):
    """Resource usage on the effect's own pairs (descriptive; ``resource-usage@8``)."""

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


STANDARDIZED_EFFECTS = (1.0, 0.5, 0.25)


class SampleSizeBasis(AnalysisModel):
    formula: str = f"n = ceil({POWER_CONSTANT} * (SD / delta)^2)"
    power: float = 0.8
    alpha: float = 0.05
    standardized: dict[str, int]

    @classmethod
    def standard(cls) -> SampleSizeBasis:
        """The pairs needed at the standardized effect sizes (SD 1)."""
        return cls(standardized={f"{d:g}": pairs_for_power(1.0, d) for d in STANDARDIZED_EFFECTS})


class Effect(AnalysisModel):
    metric: str
    label: str
    view: str
    direction: Direction
    kind: Kind | None
    """The lock's declared type for this outcome and problem; None when it declares none (the
    verdict is then refused)."""
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
    test: ExactTest | None = None
    cost: CostBeside
    sample_size: SampleSize | None = None
    pairs: tuple[Pair, ...] = ()


@dataclass(frozen=True)
class Slot:
    problem: str
    attempt: int
    baseline_arm: str
    treatment_arm: str
    baseline: TrialOutcome | None
    treatment: TrialOutcome | None


ValueOf = Callable[[TrialOutcome], Value]


@dataclass
class _Tally:
    arms: tuple[str, ...] = ()
    planned: int = 0
    not_run: int = 0
    infra_error: int = 0
    unassessed: int = 0
    not_applicable: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    excluded: dict[int, int] = field(default_factory=dict)
    causes: dict[tuple[int, str], int] = field(default_factory=dict)

    def charge(self, side: int, causes: Sequence[str]) -> None:
        """Count a slot excluded in the arm on ``side`` (0 baseline), under each of its causes."""
        self.excluded[side] = self.excluded.get(side, 0) + 1
        for cause in causes:
            self.causes[(side, cause)] = self.causes.get((side, cause), 0) + 1

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
            arms=tuple(
                ArmExclusions(
                    arm=arm,
                    planned=self.planned,
                    excluded=self.excluded.get(index, 0),
                    causes={
                        cause: count
                        for (side, cause), count in sorted(self.causes.items())
                        if side == index
                    },
                )
                for index, arm in enumerate(self.arms)
            ),
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
    for side, value in enumerate((left, right)):
        if value.value is None:
            cause = "unassessed" if value.status == UNASSESSED else f"ineligible:{value.status}"
            tally.charge(side, (cause,))
    return True


def _charge_missing_outcomes(slot: Slot, tally: _Tally) -> None:
    tally.not_run += 1
    for side, outcome in enumerate((slot.baseline, slot.treatment)):
        if outcome is None:
            tally.charge(side, ("not_run",))


def _charge_infra_errors(baseline: Any, treatment: Any, tally: _Tally) -> None:
    tally.infra_error += 1
    for side, outcome in enumerate((baseline, treatment)):
        if outcome.infra_error:
            reasons = outcome.infra_reasons or ("unrecorded",)
            tally.charge(side, tuple(f"infra_error:{reason}" for reason in reasons))


def _pair_or_exclude(slot: Slot, value_of: ValueOf, tally: _Tally) -> Pair | None:
    tally.planned += 1
    baseline, treatment = slot.baseline, slot.treatment
    if baseline is None or treatment is None:
        _charge_missing_outcomes(slot, tally)
        return None
    if baseline.infra_error or treatment.infra_error:
        _charge_infra_errors(baseline, treatment, tally)
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
    tally = _Tally(arms=(slots[0].baseline_arm, slots[0].treatment_arm) if slots else ())
    pairs = tuple(
        pair for slot in slots if (pair := _pair_or_exclude(slot, value_of, tally)) is not None
    )
    return pairs, tally.exclusions(len(pairs))


def mcnemar_test(table: McNemar) -> ExactTest:
    return ExactTest(
        name=ExactTestName.MCNEMAR,
        p_value=table.p_value,
        statistic=float(table.treatment_only - table.baseline_only),
        informative=table.baseline_only + table.treatment_only,
    )


def sign_flip_test(terms: Sequence[Fraction], state_limit: int) -> ExactTest:
    p_value = sign_flip_exact(terms, state_limit)
    return ExactTest(
        name=ExactTestName.SIGN_FLIP,
        p_value=p_value,
        statistic=float(sum(terms, Fraction(0))),
        informative=sum(1 for term in terms if term != 0),
        note=None
        if p_value is not None
        else (
            f"more than {state_limit} distinct partial sums to enumerate (the execution safety "
            "limit, --exact-state-limit; no approximation is substituted)"
        ),
    )


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


def exclusion_refusal(exclusions: Exclusions, settings: Settings) -> str | None:
    """Why the per-arm exclusion rates forbid a verdict, or None when they allow one.

    The material difference is pre-registered by the lock; without it no rate difference can
    be called acceptable, and a non-significant difference never does.
    """
    if exclusions.fraction > settings.max_exclusion_fraction:
        return (
            f"{exclusions.excluded}/{exclusions.planned} planned pairs excluded "
            f"> limit {settings.max_exclusion_fraction:.0%}"
        )
    limit = settings.max_exclusion_difference
    if limit is None:
        return (
            "no pre-registered material difference in per-arm exclusion rates "
            "(analysis.max_exclusion_difference is unset)"
        )
    if len(exclusions.arms) < 2:
        return "per-arm exclusion rates are unavailable"
    low = min(exclusions.arms, key=lambda arm: arm.rate)
    high = max(exclusions.arms, key=lambda arm: arm.rate)
    if high.rate - low.rate > limit:
        return (
            f"exclusion rates differ materially between arms: {high.arm} {high.rate:.1%} "
            f"({high.excluded}/{high.planned}) vs {low.arm} {low.rate:.1%} "
            f"({low.excluded}/{low.planned}) > limit {limit:.1%} "
            f"({settings.origin('max_exclusion_difference')})"
        )
    return None


def declared_type(settings: Settings, problem: str, metric_id: str) -> Kind | str:
    """The lock's declared type of ``metric_id`` on ``problem`` (or the aggregate ``*``).

    Declarations are written at lock time from the bound checkpoint count and the metric's
    definition (``declared_outcome_types``); a missing one is a refusal reason (a string),
    never a guess from the observed values.
    """
    declared = settings.outcome_types
    if not declared:
        return "the lock declares no outcome types (analysis.outcome_types); relock"
    entry = (
        declared.get("aggregate")
        if problem == AGGREGATE
        else (declared.get("problems") or {}).get(problem)
    )
    binary = entry.get("binary") if isinstance(entry, Mapping) else None
    if not isinstance(binary, list):
        return f"the lock declares no outcome types for {problem}"
    return Kind.BINARY if metric_id in binary else Kind.CONTINUOUS


def decide(
    metric: MetricDef,
    summary: Summary,
    exclusions: Exclusions,
    settings: Settings,
    test: ExactTest | str,
) -> tuple[Verdict, str]:
    """The verdict and why, in the documented precedence.

    The test comes from the outcome's declared type, never from the observed values; the
    interval in ``summary`` is not consulted. ``test`` is a refusal reason when the lock
    declares no usable type.
    """
    if summary.n < settings.min_pairs:
        return Verdict.INSUFFICIENT, (
            f"{summary.n} pairs < minimum {settings.min_pairs} ({settings.origin('min_pairs')})"
        )
    refusal = exclusion_refusal(exclusions, settings)
    if refusal is not None:
        return Verdict.INSUFFICIENT, refusal
    if isinstance(test, str):
        return Verdict.INSUFFICIENT, test
    if test.p_value is None:
        return Verdict.INSUFFICIENT, f"{test.label} is not computable: {test.note}"
    if test.p_value > settings.alpha:
        return Verdict.INCONCLUSIVE, f"{test.label} p={test.p_value:.4g} > {settings.alpha}"
    reason = f"{test.label} p={test.p_value:.4g} <= {settings.alpha}"
    return _directed(metric.direction, test.statistic, reason)


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
    kind = declared_type(settings, problem, metric.id)
    mcnemar = mcnemar_table(pairs) if kind is Kind.BINARY else None
    test: ExactTest | str
    if mcnemar is not None:
        test = mcnemar_test(mcnemar)
    elif kind is Kind.CONTINUOUS:
        test = sign_flip_test(
            [Fraction(pair.delta) for pair in pairs], settings.sign_flip_state_limit
        )
    else:
        test = kind
    verdict, reason = decide(metric, summary, exclusions, settings, test)
    cost = cost_beside(pairs, slots, settings, label=f"{label}:{problem}:{metric.id}")
    return Effect(
        metric=metric.id,
        label=metric.label,
        view=metric.view.value,
        direction=metric.direction,
        kind=kind if isinstance(kind, Kind) else None,
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
        test=test if isinstance(test, ExactTest) else None,
        cost=cost,
        sample_size=sample_size(metric, summary, _per_pair_cost(cost), settings),
        pairs=pairs,
    )


def _merge_arm_exclusions(effects: Sequence[Effect]) -> dict[str, ArmExclusions]:
    arms: dict[str, ArmExclusions] = {}
    for effect in effects:
        for arm in effect.exclusions.arms:
            seen = arms.get(arm.arm)
            arms[arm.arm] = arm if seen is None else seen.plus(arm)
    return arms


def _merge_status_counts(effects: Sequence[Effect]) -> dict[str, int]:
    statuses: dict[str, int] = {}
    for effect in effects:
        for status, count in effect.exclusions.ineligible_statuses.items():
            statuses[status] = statuses.get(status, 0) + count
    return statuses


def _summed_exclusions(effects: Sequence[Effect]) -> Exclusions:
    statuses = _merge_status_counts(effects)
    arms = _merge_arm_exclusions(effects)
    return Exclusions(
        planned=sum(e.exclusions.planned for e in effects),
        paired=sum(e.exclusions.paired for e in effects),
        not_run=sum(e.exclusions.not_run for e in effects),
        infra_error=sum(e.exclusions.infra_error for e in effects),
        unassessed=sum(e.exclusions.unassessed for e in effects),
        ineligible=sum(e.exclusions.ineligible for e in effects),
        ineligible_statuses=dict(sorted(statuses.items())),
        not_applicable=sum(e.exclusions.not_applicable for e in effects),
        arms=tuple(arms[name] for name in sorted(arms)),
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


def _aggregate_cost(costs: Sequence[CostBeside]) -> CostBeside:
    return CostBeside(
        n=sum(cost.n for cost in costs),
        unavailable=sum(cost.unavailable for cost in costs),
        baseline_mean=mean_if_complete([cost.baseline_mean for cost in costs]),
        treatment_mean=mean_if_complete([cost.treatment_mean for cost in costs]),
        delta_mean=mean_if_complete([cost.delta_mean for cost in costs]),
        delta_ci=None,
    )


def _equal_weight_terms(effects: Sequence[Effect]) -> list[Fraction]:
    """Every pair's delta weighted ``1 / n_pairs`` of its problem: the equal-weight mean's terms."""
    return [
        Fraction(pair.delta) / effect.n_pairs
        for effect in effects
        if effect.n_pairs
        for pair in effect.pairs
    ]


def aggregate_effect(metric: MetricDef, effects: Sequence[Effect], settings: Settings) -> Effect:
    """Across problems: estimate within each problem, then weight problems equally.

    ``n_pairs`` is the smallest problem's pair count, so ``insufficient``
    applies when any problem is short; exclusions are summed per arm and cause.
    The test is chosen from the lock's declared type of the aggregate, which is continuous
    (a mean over problems never has {0, 1} support): the exact sign-flip test on every pair's
    delta weighted by 1 / its problem's pair count, i.e. on the equal-weight mean's own terms,
    so the verdict and the mean beside it estimate one thing. The pair (problem, attempt) is
    the exchangeable unit. The equal-weight interval stays a description. A missing
    declaration, or one that declares the aggregate binary, refuses the verdict.
    """
    summary = _equal_weight_summary(effects, 1.0 - settings.alpha)
    exclusions = _summed_exclusions(effects)
    kind = declared_type(settings, AGGREGATE, metric.id)
    test: ExactTest | str
    if kind is Kind.CONTINUOUS:
        test = sign_flip_test(_equal_weight_terms(effects), settings.sign_flip_state_limit)
    elif kind is Kind.BINARY:
        test = "the lock declares the aggregate binary, which has no defined test"
    else:
        test = kind
    verdict, reason = decide(metric, summary, exclusions, settings, test)
    return Effect(
        metric=metric.id,
        label=metric.label,
        view=metric.view.value,
        direction=metric.direction,
        kind=kind if isinstance(kind, Kind) else None,
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
        test=test if isinstance(test, ExactTest) else None,
        cost=_aggregate_cost([effect.cost for effect in effects]),
        sample_size=_aggregate_sample_size(metric, effects, settings),
    )
