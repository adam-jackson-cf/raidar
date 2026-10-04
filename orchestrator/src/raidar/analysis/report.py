"""The analysis report: every comparison against the baseline arm (F10, J1, J2).

``analyse`` is pure: it takes the planned slots and the per-trial
outcomes (``raidar.analysis.load`` reads them from jobs and the Assessment
store) and returns a deterministic ``AnalysisReport``, the machine-readable
document the review surface projects (WS-K) and the gate packet includes.
Jobs and Assessments are only read, never written.

Each treatment arm is compared with the experiment's baseline arm. Effects are
listed per outcome metric: one per problem, plus an equal-weight aggregate when
the experiment has several problems. Each comparison's ``primary`` effect is
the pre-registered primary outcome of the experiment's progression (memo §2.3,
§10.1: C under ``continue``, ``reached`` under ``halt_on_failure``); the
composite S of the headline metric is a secondary effect (F15 makes it
the review surface's score, not the primary outcome).

The verdict thresholds come from the caller (``Settings``); the report says
where they came from and which of them differ from the documented defaults.

When the lock declares ``gate-effort@1``, every arm carries its gate-effort
descriptives and every comparison the gate effects both arms measured
(``raidar.analysis.gate_effort``). The manipulation check runs the lock's
pre-registered ``analysis.manipulation_checks`` when it declares them.

The experiment's kind shapes the report, never the verdicts: a ``benchmark``
adds the ranking view (``raidar.analysis.ranking``) beside the paired verdicts
against its reference agent, and a ``smoke`` report is stamped not citable
(a single-arm smoke has no comparison).
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
    metric_applies,
    problem_effect,
)
from raidar.analysis.gate_effort import GateDescriptive, arm_gate_effort, gate_effects, gate_means
from raidar.analysis.manipulation import (
    CHECK_SCORERS,
    DEFAULT_CHECKS,
    GATE_SOURCE,
    PROCESS_SOURCE,
    CheckRequest,
    ManipulationReport,
    check_report,
)
from raidar.analysis.outcomes import (
    COST_METRICS,
    OUTCOME_METRICS,
    AnalysisModel,
    TrialOutcome,
    primary_metric,
)
from raidar.analysis.ranking import Ranking, ranking
from raidar.analysis.stats import POWER_CONSTANT, pairs_for_power
from raidar.scorers.sdk.result import AssessResult

REPORT_KIND = "raidar-analysis-report"
STANDARDIZED_EFFECTS = (1.0, 0.5, 0.25)


@dataclass(frozen=True)
class PlannedTrial:
    trial_id: str
    arm: str
    attempt: int
    problem: str


NOT_CITABLE_KINDS = ("smoke",)


class DevRun(AnalysisModel):
    """Development options the analysed jobs were executed with (never arm evidence)."""

    max_checkpoints: int | None
    agent_override: str | None
    note: str


@dataclass(frozen=True)
class AnalysisInputs:
    experiment: str
    lock_sha256: str
    jobs: tuple[str, ...]
    metric: Mapping[str, object]
    baseline_arm: str
    progression: str
    planned: tuple[PlannedTrial, ...]
    outcomes: tuple[TrialOutcome, ...]
    replaced: tuple[str, ...] = ()
    process_results: Mapping[str, AssessResult | None] | None = field(default=None)
    gate_results: Mapping[str, AssessResult | None] | None = field(default=None)
    """``gate-effort@1`` results per trial when the lock declares it (else None)."""
    manipulation_checks: tuple[CheckRequest, ...] | None = None
    """The lock's pre-registered manipulation checks (None: the process defaults)."""
    dev_job: DevRun | None = None
    kind: str = "ab-test"
    rank_by: str | None = None
    attempts: int = 1
    errata: Mapping[str, Mapping[str, object]] | None = None


class ArmSummary(AnalysisModel):
    arm: str
    planned: int
    run: int
    valid: int
    infra_error: int
    not_run: int
    infra_reasons: dict[str, int]
    cost_means: dict[str, float | None]
    gate_effort: dict[str, GateDescriptive] | None = None
    """Per gate metric over the arm's valid trials; None when the lock declares no gate-effort."""


class Comparison(AnalysisModel):
    baseline: str
    treatment: str
    primary: Effect
    effects: tuple[Effect, ...]
    curves: tuple[Curve, ...]
    manipulation: ManipulationReport | None = None
    gate_effects: tuple[Effect, ...] = ()
    gate_note: str | None = None


class SampleSizeBasis(AnalysisModel):
    formula: str = f"n = ceil({POWER_CONSTANT} * (SD / delta)^2)"
    power: float = 0.8
    alpha: float = 0.05
    standardized: dict[str, int]


class ReportSettings(AnalysisModel):
    source: str = "caller"
    pinned_by_lock: bool = False
    overridden: tuple[str, ...] = ()
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
    schema_version: int = 3
    experiment: str
    experiment_kind: str = "ab-test"
    citable: bool = True
    lock_sha256: str
    jobs: tuple[str, ...]
    metric: dict[str, object]
    primary_metric: str
    baseline_arm: str
    progression: str
    dev_job: DevRun | None = None
    settings: ReportSettings
    arms: tuple[ArmSummary, ...]
    replaced_trials: tuple[str, ...] = ()
    comparisons: tuple[Comparison, ...]
    ranking: Ranking | None = None
    errata: dict[str, dict[str, object]] | None = None
    """``{problem ref: {sha256, exclude_from_solved}}`` the lock pins; only the ``*_errata``
    outcomes apply it. Absent (not written) when the lock pins no errata."""
    sample_size_basis: SampleSizeBasis = Field(
        default_factory=lambda: SampleSizeBasis(
            standardized={f"{d:g}": pairs_for_power(1.0, d) for d in STANDARDIZED_EFFECTS}
        )
    )


def _slots(inputs: AnalysisInputs, treatment: str) -> dict[str, list[Slot]]:
    """(problem, attempt) slots the plan scheduled for both arms, by problem."""
    by_key = {(o.arm, o.problem, o.attempt): o for o in inputs.outcomes}

    def scheduled(arm: str) -> set[tuple[str, int]]:
        return {(t.problem, t.attempt) for t in inputs.planned if t.arm == arm}

    keys = sorted(scheduled(inputs.baseline_arm) & scheduled(treatment))
    slots: dict[str, list[Slot]] = {}
    for problem, attempt in keys:
        slots.setdefault(problem, []).append(
            Slot(
                problem=problem,
                attempt=attempt,
                baseline=by_key.get((inputs.baseline_arm, problem, attempt)),
                treatment=by_key.get((treatment, problem, attempt)),
            )
        )
    return slots


def _effects(slots: Mapping[str, list[Slot]], settings: Settings, label: str) -> list[Effect]:
    effects: list[Effect] = []
    for metric in OUTCOME_METRICS:
        computed = [
            problem_effect(metric, problem, problem_slots, settings, label=label)
            for problem, problem_slots in slots.items()
        ]
        # A metric undefined for a problem (R/G at K=1) has no row and stays out of
        # the cross-problem aggregate, instead of reading as "ineligible" everywhere.
        per_problem = [effect for effect in computed if metric_applies(effect)]
        effects.extend(per_problem)
        if len(per_problem) > 1:
            effects.append(aggregate_effect(metric, per_problem, settings))
    return effects


def _arm_results(
    inputs: AnalysisInputs, by_trial: Mapping[str, AssessResult | None] | None, treatment: str
) -> dict[str, list[AssessResult]]:
    """One scorer's results of the two arms' valid trials."""
    results: dict[str, list[AssessResult]] = {inputs.baseline_arm: [], treatment: []}
    for outcome in inputs.outcomes:
        result = (by_trial or {}).get(outcome.trial_id)
        if outcome.arm in results and not outcome.infra_error and result is not None:
            results[outcome.arm].append(result)
    return results


def _manipulation(
    inputs: AnalysisInputs, treatment: str, settings: Settings
) -> ManipulationReport | None:
    requests = inputs.manipulation_checks
    if requests is None:
        if inputs.process_results is None:
            return None
        requests = tuple(CheckRequest(check=check) for check in DEFAULT_CHECKS)
    sources = {PROCESS_SOURCE: inputs.process_results, GATE_SOURCE: inputs.gate_results}
    per_check = {
        request.check: _arm_results(
            inputs, sources.get(CHECK_SCORERS.get(request.check, ("",))[0]), treatment
        )
        for request in requests
    }
    return check_report(
        per_check,
        requests,
        guided=treatment,
        baseline=inputs.baseline_arm,
        alpha=settings.alpha,
        preregistered=inputs.manipulation_checks is not None,
    )


def _comparison(inputs: AnalysisInputs, treatment: str, settings: Settings) -> Comparison:
    label = f"{inputs.baseline_arm}->{treatment}"
    slots = _slots(inputs, treatment)
    effects = _effects(slots, settings, label)
    primary = primary_metric(inputs.progression)
    gated, note = (
        gate_effects(slots, settings, label=label)
        if inputs.gate_results is not None
        else ((), None)
    )
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
        gate_effects=gated,
        gate_note=note,
    )


def _reason_counts(outcomes: Sequence[TrialOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for outcome in outcomes:
        for reason in outcome.infra_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _cost_means(valid: Sequence[TrialOutcome]) -> dict[str, float | None]:
    means: dict[str, float | None] = {}
    for name in COST_METRICS:
        values = [outcome.metric(name).value for outcome in valid]
        means[name] = mean_or_none([value for value in values if value is not None])
    return means


def _arm_summary(arm: str, inputs: AnalysisInputs, settings: Settings) -> ArmSummary:
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
        gate_effort=(
            arm_gate_effort(arm, valid, settings) if inputs.gate_results is not None else None
        ),
    )


def _settings_out(settings: Settings) -> ReportSettings:
    return ReportSettings(
        source=settings.source,
        pinned_by_lock=settings.pinned_by_lock,
        overridden=settings.overridden,
        changed_from_defaults=settings.changed_from_defaults(),
        min_pairs=settings.min_pairs,
        max_exclusion_fraction=settings.max_exclusion_fraction,
        alpha=settings.alpha,
        confidence=1.0 - settings.alpha,
        bootstrap_samples=settings.bootstrap_samples,
        seed=settings.seed,
        min_effects=dict(sorted(settings.min_effects.items())),
    )


def arms_of(planned: Sequence[PlannedTrial]) -> list[str]:
    return sorted({trial.arm for trial in planned})


def check_steps(outcomes: Sequence[TrialOutcome]) -> None:
    """Every trial of one problem must cover the same checkpoints to be compared."""
    steps: dict[str, set[tuple[str, ...]]] = {}
    for outcome in outcomes:
        steps.setdefault(outcome.problem, set()).add(outcome.steps)
    mixed = sorted(problem for problem, lists in steps.items() if len(lists) > 1)
    if mixed:
        raise ValueError(
            f"trials of {', '.join(mixed)} cover different checkpoint lists "
            "(truncated and full jobs cannot be compared)"
        )


def analyse(inputs: AnalysisInputs, settings: Settings) -> AnalysisReport:
    """Paired comparisons of every treatment arm against the baseline arm."""
    check_steps(inputs.outcomes)
    arms = arms_of(inputs.planned)
    if inputs.baseline_arm not in arms:
        raise ValueError(f"baseline arm {inputs.baseline_arm!r} is not in the plan ({arms})")
    treatments = [arm for arm in arms if arm != inputs.baseline_arm]
    if not treatments and inputs.kind != "smoke":
        raise ValueError("the plan has no treatment arm to compare with the baseline")
    summaries = tuple(_arm_summary(arm, inputs, settings) for arm in arms)
    return AnalysisReport(
        experiment=inputs.experiment,
        experiment_kind=inputs.kind,
        citable=inputs.kind not in NOT_CITABLE_KINDS,
        lock_sha256=inputs.lock_sha256,
        jobs=inputs.jobs,
        metric=dict(inputs.metric),
        primary_metric=primary_metric(inputs.progression),
        baseline_arm=inputs.baseline_arm,
        progression=inputs.progression,
        dev_job=inputs.dev_job,
        settings=_settings_out(settings),
        arms=summaries,
        replaced_trials=inputs.replaced,
        comparisons=tuple(_comparison(inputs, arm, settings) for arm in treatments),
        ranking=_ranking(inputs, settings, summaries),
        errata={ref: dict(pinned) for ref, pinned in inputs.errata.items()}
        if inputs.errata
        else None,
    )


def _ranking(
    inputs: AnalysisInputs, settings: Settings, summaries: Sequence[ArmSummary]
) -> Ranking | None:
    if inputs.kind != "benchmark":
        return None
    return ranking(
        inputs.outcomes,
        [summary.arm for summary in summaries],
        rank_by=inputs.rank_by or primary_metric(inputs.progression),
        reference_arm=inputs.baseline_arm,
        attempts=inputs.attempts,
        settings=settings,
        cost_means={summary.arm: summary.cost_means for summary in summaries},
        gate_means={summary.arm: gate_means(summary.gate_effort) for summary in summaries},
    )
