"""``code-quality@3``'s manifest and its per-transition count of cases G left out."""

from __future__ import annotations

from raidar.scorers.code_quality.checkpoint import Gap
from raidar.scorers.code_quality.transition import TransitionMeasure
from raidar.scorers.code_quality_v2.scorer import CODE_QUALITY_V2
from raidar.scorers.code_quality_v3.parity import CASE_EXCLUDED, MAX_EXCLUDED_CASES
from raidar.scorers.code_quality_v3.transition import CaseLevelCoverageReader
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)
from raidar.scorers.sdk.result import AssessStatus, Observation, transition_subject

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


def excluded_observation(
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


__all__ = ["CODE_QUALITY_V3", "EXCLUDED_METRIC", "NO_RUN_EVIDENCE", "excluded_observation"]
