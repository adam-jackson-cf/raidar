"""Experiment-level Quality view, per problem: curves, paired Δ, distributions, H2H, sensitivity.

Runs are pooled only under one lock: an experiment's runs are grouped by the
``lock_sha256`` they were executed under, as WS-J's ``check_jobs`` requires,
and each group is its own ``QualityExperiment``.

Which trials count (each exclusion is listed, never silent):

- an invalid trial (execution-validity gate failed) is infra-excluded;
- a trial run under an agent override (for example Harbor's ``oracle``) is
  not the declared agent and stays out of arm statistics;
- a trial truncated by ``max_checkpoints`` is a development job with no
  problem-level outcome and stays out of arm statistics;
- when one (arm, attempt) was run more than once, the latest run counts and the
  earlier ones are superseded.

Curve points carry WS-J's 95% interval (``raidar.analysis.stats.summarise``,
as ``analysis.curves`` bands do) and paired Δ uses WS-J's ``decide`` with the
directions of WS-J's outcome metrics.

The head-to-head scatter is the SCB dashboard's two signals (docs/reboot/
h2h-signals.md), re-sourced from ``code-quality@1``: Ruff findings under the
fork's contract rule set (Ruff 0.8.6 SIM allowlist, not the dashboard's
~85-selector set), and functions or methods with cc > 10 as measured by the
fork's production-quality evaluator (not the dashboard's tree-sitter count).
Each point is one checkpoint: the median across attempts of arm A (x) and arm B
(y). Lower is better for both.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from raidar.analysis.effects import Exclusions, Settings, decide
from raidar.analysis.outcomes import (
    CORRECTNESS_METRIC,
    HEADLINE_SCORE,
    OUTCOME_METRICS,
    QUALITY_COMPONENTS,
    RAW_COUNTERPART,
    MetricDef,
    View,
    primary_metric,
    primary_source,
)
from raidar.analysis.stats import seed_for, summarise
from raidar.analysis.thresholds import settings_from_lock
from raidar.review.quality import stats
from raidar.review.quality.models import (
    COMPONENTS,
    Correlation,
    CurvePoint,
    Distribution,
    HeadToHead,
    HeadToHeadSignal,
    PairedDelta,
    ProblemQuality,
    QualityExperiment,
    QualityRunDetail,
    ScatterPoint,
    Sensitivity,
    SignalDistributions,
)
from raidar.review.quality.sources import TrialSource
from raidar.scorers.sdk.manifest import Direction

_SCORE_SCALE = Decimal(30)  # S loses 100 * 0.30 * C * w * x when a weight is zeroed.
_COMPONENT_METRIC = {"C": CORRECTNESS_METRIC, "S": HEADLINE_SCORE} | {
    key: name for name, key in QUALITY_COMPONENTS.items()
}
_METRICS = {metric.id: metric for metric in OUTCOME_METRICS}


@dataclass(frozen=True)
class ReviewedTrial:
    source: TrialSource
    detail: QualityRunDetail

    @property
    def arm(self) -> str:
        return self.source.record.trial.arm

    @property
    def attempt(self) -> int:
        return self.source.record.trial.attempt


def _counted(
    trials: Sequence[ReviewedTrial],
) -> tuple[list[ReviewedTrial], list[str], set[tuple[str, int]]]:
    excluded: list[str] = []
    infra: set[tuple[str, int]] = set()
    latest: dict[tuple[str, int], ReviewedTrial] = {}
    for trial in trials:
        record, name = trial.source.record, trial.detail.run.id
        if not record.valid:
            excluded.append(f"{name}: invalid trial (infra-excluded)")
            infra.add((trial.arm, trial.attempt))
        elif record.agent_override:
            excluded.append(f"{name}: agent override {record.agent_override}")
        elif trial.source.truncation is not None:
            excluded.append(f"{name}: development job, {trial.source.truncation}")
        else:
            key = (trial.arm, trial.attempt)
            current = latest.get(key)
            if current is None or trial.source.finished_at >= current.source.finished_at:
                if current is not None:
                    excluded.append(f"{current.detail.run.id}: superseded by {name}")
                latest[key] = trial
            else:
                excluded.append(f"{name}: superseded by {current.detail.run.id}")
    counted = sorted(latest.values(), key=lambda trial: (trial.arm, trial.attempt))
    return counted, excluded, infra - set(latest)


def _curve_point(
    checkpoint: str, values: Sequence[float], label: str, settings: Settings
) -> CurvePoint:
    """Mean across attempts with WS-J's 95% interval (paired t, bootstrap from 10 values)."""
    summary = summarise(
        values,
        seed=seed_for(settings.seed, f"quality-curve:{label}:{checkpoint}"),
        bootstrap_samples=settings.bootstrap_samples,
    )
    interval = summary.interval
    return CurvePoint(
        checkpoint=checkpoint,
        n=summary.n,
        mean=summary.mean,
        sd=summary.sd,
        low=interval.low if interval else None,
        high=interval.high if interval else None,
        interval_method=interval.method.value if interval else None,
    )


def _curves(
    trials: Sequence[ReviewedTrial],
    arms: Sequence[str],
    checkpoints: Sequence[str],
    settings: Settings,
) -> dict[str, dict[str, tuple[CurvePoint, ...]]]:
    curves: dict[str, dict[str, tuple[CurvePoint, ...]]] = {}
    for arm in arms:
        values: dict[tuple[str, str], list[float]] = defaultdict(list)
        for trial in trials:
            if trial.arm != arm:
                continue
            for row in trial.detail.rows:
                for component, cell in row.cells.items():
                    if cell.value is not None:
                        values[(component, row.checkpoint)].append(cell.value)
        curves[arm] = {
            component: tuple(
                _curve_point(
                    checkpoint, values[(component, checkpoint)], f"{arm}:{component}", settings
                )
                for checkpoint in checkpoints
            )
            for component in COMPONENTS
        }
    return curves


def _metric(component: str) -> MetricDef:
    """WS-J's outcome metric for a component (its declared direction); I has none there."""
    metric = _METRICS.get(_COMPONENT_METRIC.get(component, ""))
    if metric is not None:
        return metric
    return MetricDef(component, f"Quality {component}", View.QUALITY, Direction.HIGHER_BETTER, 0.05)


def _pair_component(
    component: str,
    pairs: Sequence[tuple[int, ReviewedTrial | None, ReviewedTrial | None]],
    infra: set[tuple[str, int]],
    arms: tuple[str, str],
) -> tuple[list[float], Exclusions, list[str]]:
    """Within-attempt differences, tallied the way WS-J tallies exclusions."""
    differences: list[float] = []
    reasons: list[str] = []
    tally = {"not_run": 0, "infra_error": 0, "ineligible": 0}
    statuses: dict[str, int] = defaultdict(int)
    for attempt, base, other in pairs:
        if base is None or other is None:
            missing = arms[0] if base is None else arms[1]
            cause = "infra_error" if (missing, attempt) in infra else "not_run"
            tally[cause] += 1
            reasons.append(f"attempt {attempt}: {missing} {cause.replace('_', ' ')}")
            continue
        base_cell, other_cell = base.detail.problem[component], other.detail.problem[component]
        if base_cell.value is None or other_cell.value is None:
            gap = base_cell if base_cell.value is None else other_cell
            tally["ineligible"] += 1
            statuses[gap.status] += 1
            reasons.append(f"attempt {attempt}: {gap.status}")
            continue
        differences.append(other_cell.value - base_cell.value)
    exclusions = Exclusions(
        planned=len(pairs), paired=len(differences), ineligible_statuses=dict(statuses), **tally
    )
    return differences, exclusions, reasons


def _delta(
    component: str,
    arm: str,
    compared: tuple[list[float], Exclusions, list[str]],
    settings: Settings,
) -> PairedDelta:
    differences, exclusions, reasons = compared
    summary = summarise(
        differences,
        seed=seed_for(settings.seed, f"quality:{arm}:{component}"),
        bootstrap_samples=settings.bootstrap_samples,
    )
    verdict, why = decide(_metric(component), summary, exclusions, settings)
    interval = summary.interval
    return PairedDelta(
        component=component,
        arm=arm,
        n=summary.n,
        mean=summary.mean,
        sd=summary.sd,
        ci=(interval.low, interval.high) if interval else None,
        interval_method=interval.method.value if interval else None,
        verdict=verdict.value,
        reason=why,
        excluded=tuple(reasons),
    )


def _paired(
    trials: Sequence[ReviewedTrial],
    arms: Sequence[str],
    baseline: str | None,
    infra: set[tuple[str, int]],
    settings: Settings,
) -> tuple[PairedDelta, ...]:
    if baseline is None or baseline not in arms:
        return ()
    by_attempt = {(trial.arm, trial.attempt): trial for trial in trials}
    attempts = sorted({trial.attempt for trial in trials} | {attempt for _, attempt in infra})
    deltas: list[PairedDelta] = []
    for arm in arms:
        if arm == baseline:
            continue
        pairs = [
            (attempt, by_attempt.get((baseline, attempt)), by_attempt.get((arm, attempt)))
            for attempt in attempts
        ]
        deltas.extend(
            _delta(
                component, arm, _pair_component(component, pairs, infra, (baseline, arm)), settings
            )
            for component in COMPONENTS
        )
    return tuple(deltas)


_SIGNALS: tuple[tuple[str, str, str, str], ...] = (
    (
        "lint_per_kloc",
        "Lint density",
        "findings / KLOC",
        "code-quality@1 lint_findings / production_loc (Ruff 0.8.6 SIM allowlist)",
    ),
    (
        "high_cc_mass_share",
        "CC-mass concentration",
        "share of cc·√sloc mass",
        "1 − E: mass share of functions with cc > 10 (code-quality@1)",
    ),
    (
        "high_cc_function_lines",
        "Function LOC (cc > 10)",
        "lines",
        "span lines of each cc > 10 function in the E evidence; code-quality@1 does not "
        "persist the full symbol table, so lower-complexity functions are not included",
    ),
)


def _signal_values(trial: ReviewedTrial, signal: str) -> list[float]:
    values: list[float] = []
    for row in trial.detail.rows:
        value = getattr(row.signals, signal)
        if isinstance(value, tuple):
            values.extend(float(item) for item in value)
        elif value is not None:
            values.append(float(value))
    return values


def _distributions(
    trials: Sequence[ReviewedTrial], arms: Sequence[str]
) -> tuple[SignalDistributions, ...]:
    result = []
    for signal, label, unit, source in _SIGNALS:
        per_arm = []
        for arm in arms:
            values = [
                value
                for trial in trials
                if trial.arm == arm
                for value in _signal_values(trial, signal)
            ]
            per_arm.append(
                Distribution(
                    arm=arm, n=len(values), values=tuple(values), quantiles=stats.quantiles(values)
                )
            )
        result.append(
            SignalDistributions(
                signal=signal, label=label, unit=unit, source=source, arms=tuple(per_arm)
            )
        )
    return tuple(result)


_H2H: tuple[tuple[str, str, str, str], ...] = (
    (
        "lint_findings",
        "Lint errors per checkpoint",
        "Lint findings",
        "Ruff 0.8.6, SCB contract SIM allowlist (code-quality@1)",
    ),
    (
        "high_cc_count",
        "Complexity per checkpoint",
        "Functions with CC > 10",
        "fork production-quality evaluator cc > 10 (code-quality@1)",
    ),
)


def _arm_medians(
    trials: Sequence[ReviewedTrial], arm: str, signal: str
) -> dict[str, tuple[float, int]]:
    values: dict[str, list[float]] = defaultdict(list)
    for trial in trials:
        if trial.arm != arm:
            continue
        for row in trial.detail.rows:
            value = getattr(row.signals, signal)
            if value is not None:
                values[row.checkpoint].append(float(value))
    return {checkpoint: (stats.median(items), len(items)) for checkpoint, items in values.items()}


def _scatter(
    spec: tuple[str, str, str, str],
    sides: tuple[
        tuple[str, dict[str, tuple[float, int]]], tuple[str, dict[str, tuple[float, int]]]
    ],
    checkpoints: Sequence[str],
) -> HeadToHeadSignal:
    signal, label, axis, engine = spec
    (arm_a, a), (arm_b, b) = sides
    points, excluded = [], []
    for checkpoint in checkpoints:
        absent = [arm for arm, side in sides if checkpoint not in side]
        if absent:
            excluded.append(f"{checkpoint}: not measured in {', '.join(absent)}")
            continue
        (value_a, n_a), (value_b, n_b) = a[checkpoint], b[checkpoint]
        points.append(ScatterPoint(checkpoint=checkpoint, a=value_a, b=value_b, n_a=n_a, n_b=n_b))
    return HeadToHeadSignal(
        signal=signal,
        label=label,
        axis=axis,
        engine=engine,
        points=tuple(points),
        excluded=tuple(excluded),
    )


def _head_to_head(
    trials: Sequence[ReviewedTrial],
    arms: Sequence[str],
    baseline: str | None,
    checkpoints: Sequence[str],
) -> HeadToHead | None:
    if len(arms) < 2:
        return None
    arm_a = baseline if baseline in arms else arms[0]
    assert arm_a is not None
    arm_b = next(arm for arm in arms if arm != arm_a)
    signals = tuple(
        _scatter(
            spec,
            (
                (arm_a, _arm_medians(trials, arm_a, spec[0])),
                (arm_b, _arm_medians(trials, arm_b, spec[0])),
            ),
            checkpoints,
        )
        for spec in _H2H
    )
    return HeadToHead(arm_a=arm_a, arm_b=arm_b, signals=signals)


def _eligible_components(
    trials: Sequence[ReviewedTrial],
) -> tuple[list[dict[str, float]], list[str]]:
    """Problem-level component values of trials whose headline score is eligible."""
    eligible, excluded = [], []
    for trial in trials:
        problem = trial.detail.problem
        if problem["S"].value is None:
            excluded.append(f"{trial.detail.run.id}: headline score {problem['S'].status}")
            continue
        eligible.append(
            {key: cell.value for key, cell in problem.items() if cell.value is not None}
        )
    return eligible, excluded


def _correlation(left: str, right: str, eligible: Sequence[dict[str, float]]) -> Correlation:
    if len(eligible) < 3:
        return Correlation(a=left, b=right, reason=f"n={len(eligible)} < 3")
    r = stats.pearson([cells[left] for cells in eligible], [cells[right] for cells in eligible])
    if r is None:
        return Correlation(a=left, b=right, reason="a component is constant")
    return Correlation(a=left, b=right, r=r)


def _sensitivity(trials: Sequence[ReviewedTrial], weights: dict[str, float]) -> Sensitivity:
    """Weight ablation as the fork's ``code_quality.sensitivity.weight_ablations`` sums it.

    The fork function itself needs ``ProblemScore`` objects (per-checkpoint
    passed/total counts, K >= 2, the scb-canonical@1 weights only), which the
    stored observations do not carry; the sum is the same one, over the
    eligible trials' problem-level components, in Decimal.
    """
    eligible, excluded = _eligible_components(trials)
    ablation = {
        key: float(
            sum(
                (
                    _SCORE_SCALE
                    * Decimal(str(cells["C"]))
                    * Decimal(str(weight))
                    * Decimal(str(cells[key]))
                    for cells in eligible
                ),
                Decimal(0),
            )
        )
        for key, weight in weights.items()
        if eligible
    }
    return Sensitivity(
        n=len(eligible),
        weights=weights,
        ablation=ablation,
        correlations=tuple(
            _correlation(left, right, eligible) for left, right in combinations(weights, 2)
        ),
        excluded=tuple(excluded),
    )


def _problem_notes(
    policies: Sequence[str], baseline: str | None, arms: Sequence[str], lock_note: str | None
) -> list[str]:
    notes = []
    if len(policies) > 1:
        notes.append(f"trials use different headline metrics: {', '.join(policies)}")
    if baseline is None:
        why = lock_note or "the lock names no baseline arm"
        notes.append(f"no baseline arm is known ({why}); no paired Δ")
    elif baseline not in arms:
        notes.append(f"baseline arm {baseline} has no counted trials (see Left out); no paired Δ")
    return notes


def _problem_quality(
    problem: str, trials: Sequence[ReviewedTrial], settings: Settings
) -> ProblemQuality:
    counted, excluded, infra = _counted(trials)
    # Without any Assessment a trial's checkpoint list (and so its default policy)
    # is only its planned steps; assessed trials define the problem when present.
    assessed = [trial for trial in trials if any(trial.detail.run.sources.values())] or trials
    reference = max(assessed, key=lambda trial: trial.detail.run.checkpoints)
    checkpoints = tuple(row.checkpoint for row in reference.detail.rows)
    baseline = reference.source.experiment.baseline_arm
    arms = tuple(sorted({trial.arm for trial in counted}, key=lambda arm: (arm != baseline, arm)))
    policies = sorted({trial.detail.policy.ref for trial in assessed})
    notes = _problem_notes(policies, baseline, arms, reference.source.experiment.note)
    return ProblemQuality(
        problem=problem,
        checkpoints=checkpoints,
        policy=reference.detail.policy,
        baseline_arm=baseline,
        arms=arms,
        run_ids=tuple(sorted(trial.detail.run.id for trial in trials)),
        curves=_curves(counted, arms, checkpoints, settings),
        paired=_paired(counted, arms, baseline, infra, settings),
        distributions=_distributions(counted, arms),
        head_to_head=_head_to_head(counted, arms, baseline, checkpoints),
        sensitivity=_sensitivity(counted, reference.detail.policy.inertia_weights),
        excluded=tuple(excluded),
        notes=tuple(notes),
    )


def _grouped[KeyT](
    trials: Sequence[ReviewedTrial], key: Callable[[ReviewedTrial], KeyT]
) -> dict[KeyT, list[ReviewedTrial]]:
    groups: dict[KeyT, list[ReviewedTrial]] = defaultdict(list)
    for trial in trials:
        groups[key(trial)].append(trial)
    return dict(groups)


def _lock_settings(members: Sequence[ReviewedTrial]) -> Settings:
    """The verdict thresholds the group's lock pins (WS-J defaults without a lock)."""
    return settings_from_lock(members[0].source.experiment.analysis)


def _primary(members: Sequence[ReviewedTrial]) -> tuple[str | None, str | None, str | None]:
    """(primary outcome, its source, its raw counterpart) as the analysis report labels them."""
    configured = (members[0].source.experiment.analysis or {}).get("primary_outcome")
    progression = members[0].source.record.progression.mode
    try:
        primary = primary_metric(progression, configured)
    except ValueError:
        return None, None, None
    return primary, primary_source(progression, configured), RAW_COUNTERPART.get(primary)


def build_experiments(
    trials: Sequence[ReviewedTrial], settings: Settings | None = None
) -> tuple[QualityExperiment, ...]:
    """Per experiment, lock and problem; paired Δ uses the thresholds the lock pins.

    ``settings`` replaces the lock's thresholds for every group (tests only).
    """
    experiments = []
    for (experiment, lock), members in sorted(
        _grouped(
            trials, lambda t: (t.source.record.experiment, t.source.experiment.lock_sha256 or "")
        ).items()
    ):
        problems = _grouped(members, lambda t: t.source.record.trial.problem)
        chosen = settings or _lock_settings(members)
        primary, source, raw = _primary(members)
        experiments.append(
            QualityExperiment(
                experiment=experiment,
                lock_sha256=lock or None,
                primary_outcome=primary,
                primary_source=source,
                primary_raw=raw,
                jobs=tuple(sorted({trial.source.job_id for trial in members})),
                problems=tuple(
                    _problem_quality(problem, group, chosen)
                    for problem, group in sorted(problems.items())
                ),
            )
        )
    return tuple(experiments)
