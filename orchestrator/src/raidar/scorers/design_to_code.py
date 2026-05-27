"""Design-to-code scorer implementation."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scorecard import MetricScore
from raidar.scorers.base import BaseScorer, ScorerContext, ScorerEvidence, register_scorer
from raidar.scorers.common import (
    artifact_metric_score,
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
        metric(
            "visual-regression",
            "core",
            0.34,
            evidence="Captured screenshot comparison against the reference image.",
            score_derivation="Uses the visual score computed from configured visual bands.",
            pass_fail="Passes when the visual pass policy accepts the captured output.",
        ),
        metric(
            "functional",
            "core",
            0.24,
            evidence="Build, test, and gate execution outcomes for the submitted code.",
            score_derivation="Uses the functional score computed from execution outputs.",
            pass_fail="Passes when functional execution passed.",
        ),
        metric(
            "test-coverage",
            "core",
            0.15,
            evidence="Coverage measurement and configured threshold.",
            score_derivation="Divides measured coverage by threshold, capped at 1.0.",
            pass_fail="Passes when the coverage output passed its configured threshold.",
        ),
        metric(
            "verification-stability",
            "core",
            0.10,
            evidence="Verification gate failure count across the run.",
            score_derivation="Uses the verification stability score computed from gate history.",
            pass_fail="Passes when verification stability is greater than zero.",
        ),
        metric(
            "artifact-checks",
            "artifact-checks",
            0.17,
            evidence="Required implementation artifacts in the workspace.",
            score_derivation="Divides matched required artifacts by configured required artifacts.",
            pass_fail="Passes when every required artifact exists.",
            config={"required_paths": ["src/app/page.tsx"], "path_match": "glob"},
        ),
    )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        outputs = context.execution.outputs
        return ScorerEvidence(
            metric_scores=(
                visual_regression_metric_score(outputs),
                functional_metric_score(outputs),
                design_to_code_coverage_metric_score(outputs),
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


def design_to_code_coverage_metric_score(outputs) -> MetricScore:
    coverage = outputs.test_coverage
    return MetricScore(
        metric_id="test-coverage",
        score=_design_to_code_coverage_score(coverage),
        passed=coverage.passed,
        evidence=(
            f"threshold={coverage.threshold}, "
            f"measured={coverage.measured}, "
            f"source={coverage.source}"
        ),
    )


def _design_to_code_coverage_score(coverage) -> float:
    if coverage.threshold is None:
        return 1.0 if coverage.passed else 0.0
    if coverage.measured is None:
        return 0.0
    return min(1.0, coverage.measured / coverage.threshold)
