"""The analysis report: every comparison against the baseline arm (F10, J1, J2).

``analyse`` is pure: it takes the planned slots and the per-trajectory
outcomes (``raidar.analysis.load`` reads them from runs and the Assessment
store) and returns a deterministic ``AnalysisReport``, the machine-readable
document the review surface projects (WS-K) and the gate packet includes.
Runs and Assessments are only read, never written.

Each treatment arm is compared with the experiment's baseline arm. Effects are
listed per outcome metric: one per problem, plus an equal-weight aggregate when
the experiment has several problems. Each comparison's ``primary`` effect is
the pre-registered primary outcome of the experiment's progression (memo §2.3,
§10.1: C under ``continue``, ``reached`` under ``halt_on_failure``); the
composite S of the headline ScorePolicy is a secondary effect (F15 makes it
the review surface's score, not the primary outcome).

The verdict thresholds come from the caller (``Settings``). The experiment
spec does not pin them yet, so the report says where they came from and which
of them differ from the documented defaults.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import Field

from raidar.analysis.curves import Curve, problem_curves
from raidar.analysis.effects import (
    Effect,
    Settings,
    Slot,
    aggregate_effect,
    mean_or_none,
    problem_effect,
)
from raidar.analysis.manipulation import ManipulationReport, manipulation_check
from raidar.analysis.outcomes import (
    COST_METRICS,
    OUTCOME_METRICS,
    AnalysisModel,
    TrajectoryOutcome,
    primary_metric,
)
from raidar.analysis.stats import POWER_CONSTANT, pairs_for_power
from raidar.scorers.sdk.result import AssessResult

REPORT_KIND = "raidar-analysis-report"
STANDARDIZED_EFFECTS = (1.0, 0.5, 0.25)


@dataclass(frozen=True)
class PlannedTrial:
    trial_id: str
    arm: str
    block: int
    problem: str


class DevRun(AnalysisModel):
    """Development options the analysed runs were executed with (never arm evidence)."""

    max_checkpoints: int | None
    agent_override: str | None
    note: str


@dataclass(frozen=True)
class AnalysisInputs:
    experiment: str
    lock_sha256: str
    runs: tuple[str, ...]
    headline_policy: Mapping[str, object]
    baseline_arm: str
    progression: str
    planned: tuple[PlannedTrial, ...]
    outcomes: tuple[TrajectoryOutcome, ...]
    replaced: tuple[str, ...] = ()
    process_results: Mapping[str, AssessResult | None] | None = field(default=None)
    dev_run: DevRun | None = None


class ArmSummary(AnalysisModel):
    arm: str
    planned: int
    run: int
    valid: int
    infra_error: int
    not_run: int
    infra_reasons: dict[str, int]
    cost_means: dict[str, float | None]


class Comparison(AnalysisModel):
    baseline: str
    treatment: str
    primary: Effect
    effects: tuple[Effect, ...]
    curves: tuple[Curve, ...]
    manipulation: ManipulationReport | None = None


class SampleSizeBasis(AnalysisModel):
    formula: str = f"n = ceil({POWER_CONSTANT} * (SD / delta)^2)"
    power: float = 0.8
    alpha: float = 0.05
    standardized: dict[str, int]


class ReportSettings(AnalysisModel):
    source: str = "caller"
    pinned_by_lock: bool = False
    changed_from_defaults: tuple[str, ...] = ()
    min_pairs: int
    max_exclusion_fraction: float
    alpha: float
    confidence: float = 0.95
    bootstrap_min_pairs: int = 10
    bootstrap_samples: int
    seed: int
    min_effects: dict[str, float]


class AnalysisReport(AnalysisModel):
    kind: str = REPORT_KIND
    schema_version: int = 1
    experiment: str
    lock_sha256: str
    runs: tuple[str, ...]
    headline_policy: dict[str, object]
    primary_metric: str
    baseline_arm: str
    progression: str
    dev_run: DevRun | None = None
    settings: ReportSettings
    arms: tuple[ArmSummary, ...]
    replaced_trajectories: tuple[str, ...] = ()
    comparisons: tuple[Comparison, ...]
    sample_size_basis: SampleSizeBasis = Field(
        default_factory=lambda: SampleSizeBasis(
            standardized={f"{d:g}": pairs_for_power(1.0, d) for d in STANDARDIZED_EFFECTS}
        )
    )


def _slots(inputs: AnalysisInputs, treatment: str) -> dict[str, list[Slot]]:
    """(problem, block) slots the plan scheduled for both arms, by problem."""
    by_key = {(o.arm, o.problem, o.block): o for o in inputs.outcomes}

    def scheduled(arm: str) -> set[tuple[str, int]]:
        return {(t.problem, t.block) for t in inputs.planned if t.arm == arm}

    keys = sorted(scheduled(inputs.baseline_arm) & scheduled(treatment))
    slots: dict[str, list[Slot]] = {}
    for problem, block in keys:
        slots.setdefault(problem, []).append(
            Slot(
                problem=problem,
                block=block,
                baseline=by_key.get((inputs.baseline_arm, problem, block)),
                treatment=by_key.get((treatment, problem, block)),
            )
        )
    return slots


def _effects(slots: Mapping[str, list[Slot]], settings: Settings, label: str) -> list[Effect]:
    effects: list[Effect] = []
    for metric in OUTCOME_METRICS:
        per_problem = [
            problem_effect(metric, problem, problem_slots, settings, label=label)
            for problem, problem_slots in slots.items()
        ]
        effects.extend(per_problem)
        if len(per_problem) > 1:
            effects.append(aggregate_effect(metric, per_problem, settings))
    return effects


def _manipulation(
    inputs: AnalysisInputs, treatment: str, settings: Settings
) -> ManipulationReport | None:
    if inputs.process_results is None:
        return None
    results: dict[str, list[AssessResult]] = {inputs.baseline_arm: [], treatment: []}
    for outcome in inputs.outcomes:
        result = inputs.process_results.get(outcome.trial_id)
        if outcome.arm in results and not outcome.infra_error and result is not None:
            results[outcome.arm].append(result)
    return manipulation_check(
        results, guided=treatment, baseline=inputs.baseline_arm, alpha=settings.alpha
    )


def _comparison(inputs: AnalysisInputs, treatment: str, settings: Settings) -> Comparison:
    label = f"{inputs.baseline_arm}->{treatment}"
    slots = _slots(inputs, treatment)
    effects = _effects(slots, settings, label)
    primary = primary_metric(inputs.progression)
    return Comparison(
        baseline=inputs.baseline_arm,
        treatment=treatment,
        primary=[effect for effect in effects if effect.metric == primary][-1],
        effects=tuple(effects),
        curves=tuple(
            curve
            for problem, problem_slots in slots.items()
            for curve in problem_curves(problem, problem_slots, settings, label=label)
        ),
        manipulation=_manipulation(inputs, treatment, settings),
    )


def _reason_counts(outcomes: Sequence[TrajectoryOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for reason in outcome.infra_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _cost_means(valid: Sequence[TrajectoryOutcome]) -> dict[str, float | None]:
    means: dict[str, float | None] = {}
    for name in COST_METRICS:
        values = [outcome.metric(name).value for outcome in valid]
        means[name] = mean_or_none([value for value in values if value is not None])
    return means


def _arm_summary(arm: str, inputs: AnalysisInputs) -> ArmSummary:
    planned = [trial for trial in inputs.planned if trial.arm == arm]
    ran = [outcome for outcome in inputs.outcomes if outcome.arm == arm]
    valid = [outcome for outcome in ran if not outcome.infra_error]
    return ArmSummary(
        arm=arm,
        planned=len(planned),
        run=len(ran),
        valid=len(valid),
        infra_error=len(ran) - len(valid),
        not_run=len(planned) - len(ran),
        infra_reasons=_reason_counts(ran),
        cost_means=_cost_means(valid),
    )


def _settings_out(settings: Settings) -> ReportSettings:
    return ReportSettings(
        source=settings.source,
        changed_from_defaults=settings.changed_from_defaults(),
        min_pairs=settings.min_pairs,
        max_exclusion_fraction=settings.max_exclusion_fraction,
        alpha=settings.alpha,
        bootstrap_samples=settings.bootstrap_samples,
        seed=settings.seed,
        min_effects=dict(sorted(settings.min_effects.items())),
    )


def arms_of(planned: Sequence[PlannedTrial]) -> list[str]:
    return sorted({trial.arm for trial in planned})


def check_steps(outcomes: Sequence[TrajectoryOutcome]) -> None:
    """Every trajectory of one problem must cover the same checkpoints to be compared."""
    steps: dict[str, set[tuple[str, ...]]] = {}
    for outcome in outcomes:
        steps.setdefault(outcome.problem, set()).add(outcome.steps)
    mixed = sorted(problem for problem, lists in steps.items() if len(lists) > 1)
    if mixed:
        raise ValueError(
            f"trajectories of {', '.join(mixed)} cover different checkpoint lists "
            "(truncated and full runs cannot be compared)"
        )


def analyse(inputs: AnalysisInputs, settings: Settings) -> AnalysisReport:
    """Paired comparisons of every treatment arm against the baseline arm."""
    check_steps(inputs.outcomes)
    arms = arms_of(inputs.planned)
    if inputs.baseline_arm not in arms:
        raise ValueError(f"baseline arm {inputs.baseline_arm!r} is not in the plan ({arms})")
    treatments = [arm for arm in arms if arm != inputs.baseline_arm]
    if not treatments:
        raise ValueError("the plan has no treatment arm to compare with the baseline")
    return AnalysisReport(
        experiment=inputs.experiment,
        lock_sha256=inputs.lock_sha256,
        runs=inputs.runs,
        headline_policy=dict(inputs.headline_policy),
        primary_metric=primary_metric(inputs.progression),
        baseline_arm=inputs.baseline_arm,
        progression=inputs.progression,
        dev_run=inputs.dev_run,
        settings=_settings_out(settings),
        arms=tuple(_arm_summary(arm, inputs) for arm in arms),
        replaced_trajectories=inputs.replaced,
        comparisons=tuple(_comparison(inputs, arm, settings) for arm in treatments),
    )
