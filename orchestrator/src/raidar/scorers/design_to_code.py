"""Design-to-code scorer implementation."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    artifact_metric_score,
    coverage_output_metric_score,
    functional_metric_score,
    metric,
    required_artifact_patterns,
    verification_stability_metric_score,
)


@register_scorer(id="design-to-code", version=1)
class DesignToCode(BaseScorer):
    """Design-to-code scorer retained as a code-backed definition."""

    status = "active"
    category = "quality"
    description = (
        "Scores visual design implementation tasks against reference evidence, "
        "functional evidence, artifacts, and verification quality."
    )
    metrics = (
        metric("visual-regression", "core", 0.34),
        metric("functional", "core", 0.24),
        metric("test-coverage", "core", 0.15),
        metric("verification-stability", "core", 0.10),
        metric(
            "artifact-checks",
            "artifact-checks",
            0.17,
            config={"required_paths": ["src/app/page.tsx"], "path_match": "glob"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        outputs = context.execution.outputs
        return ScorerEvidence(
            metric_scores=(
                visual_regression_metric_score(outputs),
                functional_metric_score(outputs),
                coverage_output_metric_score(outputs),
                verification_stability_metric_score(outputs),
                artifact_metric_score(
                    Path(context.workspace),
                    required_artifact_patterns(context.scenario, self.id),
                ),
            )
        )


def visual_regression_metric_score(outputs) -> MetricScore:
    if outputs.visual is None:
        return MetricScore(
            metric_id="visual-regression",
            score=0.0,
            passed=False,
            evidence="Visual threshold not configured.",
        )
    return MetricScore(
        metric_id="visual-regression",
        score=outputs.visual.score,
        passed=bool(outputs.visual.passed),
        evidence=f"similarity={outputs.visual.score}, passed={outputs.visual.passed}",
    )
