"""``code-quality@3``: code-quality@2 whose G leaves out a few flipped cases, not the step.

``code-quality@1`` and ``@2`` are released and unchanged. @3 is @2 in every
metric, status, reason and evidence item, except one: where a step's G run
evidence (the trial's own coverage pass, or the store's offline run) failed
parity only because at most ``MAX_EXCLUDED_CASES`` cases changed outcome
between the grading run and the coverage run, @3 leaves those cases out of
both runs and computes G on the rest (``raidar.scorers.code_quality_v3.parity``)
instead of reading the step as ``ineligible``. Every other parity failure stays
``ineligible`` (``coverage_parity_failed``) as in @2.

@3 also reports ``regression_parity_excluded_cases`` per transition: how many
cases its G left out (0 when parity held), with their node ids as the reason,
or a typed status when G read no run evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from raidar.scorers.code_quality.checkpoint import Gap, measure_checkpoint
from raidar.scorers.code_quality.observations import (
    ObservationSet,
    checkpoint_observations,
    transition_observations,
)
from raidar.scorers.code_quality.scorer import (
    _PROBLEM_METRICS,
    CodeQualityConfig,
    _result,
    problem_mean,
)
from raidar.scorers.code_quality.transition import TransitionMeasure
from raidar.scorers.code_quality_v2.scorer import CODE_QUALITY_V2, CodeQualityV2Scorer
from raidar.scorers.code_quality_v2.transition import measure_transitions
from raidar.scorers.code_quality_v3.parity import CASE_EXCLUDED, MAX_EXCLUDED_CASES
from raidar.scorers.code_quality_v3.transition import CaseLevelCoverageReader
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, transition_subject

EXCLUDED_METRIC = "regression_parity_excluded_cases"
NO_RUN_EVIDENCE = "no_g_run_evidence"

CODE_QUALITY_V3 = CODE_QUALITY_V2.model_copy(
    update={
        "version": "3",
        "description": (
            "SCB-fork code-quality and inertia components over the per-step snapshots "
            "(code-quality@2); G leaves out at most "
            f"{MAX_EXCLUDED_CASES} cases whose outcome differs between the grading and "
            "coverage runs instead of reading the step as ineligible."
        ),
        "metrics": (
            *CODE_QUALITY_V2.metrics,
            MetricSpec(
                id=EXCLUDED_METRIC,
                description=(
                    "Grader cases G left out at the transition because their outcome differed "
                    "between the grading run and the coverage run (0 when parity held)."
                ),
                level=MetricLevel.TRANSITION,
                type=MetricType.COUNT,
                direction=Direction.NEUTRAL,
                tier=Tier.DETERMINISTIC,
                exactness=Exactness.NATIVE,
                unit="cases",
            ),
        ),
    }
)


def _excluded_observation(
    transition: TransitionMeasure, reader: CaseLevelCoverageReader
) -> Observation:
    subject = transition_subject(transition.prior.step, transition.current.step)
    excluded = reader.excluded.get(transition.current.step)
    if excluded is None:
        regression = transition.regression
        detail = regression.reason if isinstance(regression, Gap) else "not read"
        return Observation(
            metric=EXCLUDED_METRIC,
            subject=subject,
            status=AssessStatus.NOT_APPLICABLE,
            reason=f"{NO_RUN_EVIDENCE}: {detail}",
        )
    return Observation(
        metric=EXCLUDED_METRIC,
        subject=subject,
        status=AssessStatus.OK,
        value=float(len(excluded)),
        reason=f"{CASE_EXCLUDED}: {', '.join(excluded)}" if excluded else None,
    )


@dataclass
class CodeQualityV3Scorer(CodeQualityV2Scorer):
    """code-quality@2 whose G leaves out a few flipped cases instead of the step."""

    _excluded: dict[str, tuple[str, ...]] = field(default_factory=dict, init=False, repr=False)

    @property
    def manifest(self) -> ScorerManifest:
        return CODE_QUALITY_V3

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._missing = []
        config = CodeQualityConfig.model_validate(dict(ctx.config))
        if not any(step.reached and step.snapshot is not None for step in ctx.steps):
            return statused_result(
                CODE_QUALITY_V3, ctx, AssessStatus.UNAVAILABLE, "no stored snapshot to measure"
            )
        try:
            tools = self._environment()
        except (OSError, RuntimeError, ValueError) as error:
            return statused_result(
                CODE_QUALITY_V3,
                ctx,
                AssessStatus.UNAVAILABLE,
                f"measurement_environment_mismatch: tool environment unavailable: {error}",
            )
        measures = [measure_checkpoint(ctx, step, tools, config.entry_file) for step in ctx.steps]
        reader = CaseLevelCoverageReader(self.store_root)
        observations = ObservationSet()
        for measure in measures:
            checkpoint_observations(observations, measure)
        for transition in measure_transitions(measures, reader):
            transition_observations(observations, transition)
            observations.observations.append(_excluded_observation(transition, reader))
        for metric, component in _PROBLEM_METRICS.items():
            observations.observations.append(
                problem_mean(metric, observations.by_metric(component), observations.exact)
            )
        self._missing = reader.missing
        return _result(observations, measures)


def code_quality_v3_scorer() -> CodeQualityV3Scorer:
    """Registry factory for ``code-quality@3`` without a store (offline runs unavailable)."""
    return CodeQualityV3Scorer()


def code_quality_v3_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> CodeQualityV3Scorer:
    """Store-bound factory: reads deferred steps' offline coverage runs from the store."""
    del replay_only, trial
    return CodeQualityV3Scorer(store_root=store_root)


__all__ = [
    "CODE_QUALITY_V3",
    "EXCLUDED_METRIC",
    "CodeQualityV3Scorer",
    "code_quality_v3_scorer",
    "code_quality_v3_scorer_for_store",
]
