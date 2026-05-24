"""Shared helpers for scorer implementations."""

from __future__ import annotations

from pathlib import Path

from raidar.schemas.scenario import ScorerMetricDefinition
from raidar.schemas.scorecard import MetricScore


def metric(
    metric_id: str,
    metric_type: str,
    weight: float,
    *,
    config: dict | None = None,
) -> ScorerMetricDefinition:
    return ScorerMetricDefinition(
        id=metric_id,
        type=metric_type,
        weight=weight,
        config=config or {},
    )


def functional_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="functional",
        score=outputs.functional.score,
        passed=outputs.functional.passed,
        evidence=(
            f"build={outputs.functional.build_succeeded}, "
            f"tests={outputs.functional.tests_passed}/{outputs.functional.tests_total}"
        ),
    )


def verification_stability_metric_score(outputs) -> MetricScore:
    score = outputs.verification_stability
    return MetricScore(
        metric_id="verification-stability",
        score=score.score,
        passed=score.score > 0,
        evidence=f"failures={score.total_gate_failures}",
    )


def coverage_output_metric_score(outputs) -> MetricScore:
    coverage = outputs.test_coverage
    return MetricScore(
        metric_id="test-coverage",
        score=coverage_metric_score(coverage),
        passed=coverage.passed,
        evidence=(
            f"threshold={coverage.threshold}, "
            f"measured={coverage.measured}, "
            f"source={coverage.source}"
        ),
    )


def coverage_metric_score(score) -> float:
    if score.threshold is None:
        return 1.0 if score.passed else 0.0
    if score.measured is None:
        return 0.0
    return min(1.0, score.measured / score.threshold)


def artifact_metric_score(workspace: Path, required_artifacts: tuple[str, ...]) -> MetricScore:
    missing = missing_required_artifacts(workspace, required_artifacts)
    matched_count = len(required_artifacts) - len(missing)
    score = 1.0 if not required_artifacts else matched_count / len(required_artifacts)
    return MetricScore(
        metric_id="artifact-checks",
        score=round(score, 3),
        passed=score >= 1.0,
        matched_count=matched_count,
        missing_patterns=missing,
        evidence=f"required_artifacts={len(required_artifacts)}, matched={matched_count}",
    )


def required_artifact_patterns(scenario, scorer_id: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for scorer_ref in getattr(scenario, "scorers", ()):
        if getattr(scorer_ref, "id", None) != scorer_id:
            continue
        artifact_config = getattr(scorer_ref, "config", {}).get("artifact-checks", {})
        required_paths = artifact_config.get("required_paths", [])
        if isinstance(required_paths, list):
            patterns.extend(path for path in required_paths if isinstance(path, str))
    return tuple(dict.fromkeys(patterns))


def missing_required_artifacts(workspace: Path, patterns: tuple[str, ...]) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if not any(path.is_file() for path in workspace.glob(pattern))
    ]
