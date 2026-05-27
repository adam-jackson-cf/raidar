"""Canonical metric and scorer output assembly."""

from __future__ import annotations

from raidar.runtime.models import ScorecardBuildContext
from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    GateCheck,
    MetricScore,
    PerformanceGatesScore,
    ResourceEfficiencyScore,
    ScorerMetricContribution,
    ScorerResult,
)
from raidar.scorers.base import ScorerContext, scorer_class


def build_metric_scores(
    context: ScorecardBuildContext,
    *,
    execution_validity: ExecutionValidityScore,
    resource_efficiency: ResourceEfficiencyScore,
) -> list[MetricScore]:
    metric_scores = _scorer_owned_metric_scores(
        context,
        execution_validity=execution_validity,
        resource_efficiency=resource_efficiency,
    )
    for metric in context.request.scenario.resolved_metrics():
        metric_id = metric.id
        if metric_id not in metric_scores:
            metric_scores[metric_id] = MetricScore(
                metric_id=metric_id,
                score=0.0,
                passed=False,
                evidence=f"Selected scorer did not emit metric: {metric_id}",
            )
    return [metric_scores[metric.id] for metric in context.request.scenario.resolved_metrics()]


def _scorer_owned_metric_scores(
    context: ScorecardBuildContext,
    *,
    execution_validity: ExecutionValidityScore,
    resource_efficiency: ResourceEfficiencyScore,
) -> dict[str, MetricScore]:
    scorer_context = ScorerContext(
        workspace=context.context.workspace,
        scenario_dir=context.request.scenario_dir,
        scenario=context.request.scenario,
        execution=context.execution,
        resource_efficiency=resource_efficiency,
        execution_validity=execution_validity,
        workspace_changes=context.artifacts.workspace_changes,
        retained_evidence=context.artifacts.evidence_artifacts,
    )
    metric_scores: dict[str, MetricScore] = {}
    for scorer_ref in context.request.scenario.scorers:
        scorer = scorer_class(scorer_ref.id, scorer_ref.version)()
        evidence = scorer.collect_evidence(scorer_context)
        for metric_score in evidence.metric_scores:
            score = MetricScore.model_validate(metric_score)
            metric_scores[score.metric_id] = score
    return metric_scores


def build_scorer_results(
    context: ScorecardBuildContext,
    metric_scores: list[MetricScore],
) -> list[ScorerResult]:
    score_by_id = {metric.metric_id: metric.score for metric in metric_scores}
    results: list[ScorerResult] = []
    for scorer in context.request.scenario.resolved_scorers():
        total_weight = sum(metric.weight for metric in scorer.metrics if metric.weight > 0)
        contributions: list[ScorerMetricContribution] = []
        weighted_score = 0.0
        for metric in scorer.metrics:
            score = score_by_id.get(metric.id, 0.0)
            weighted = score * metric.weight
            weighted_score += weighted
            contributions.append(
                ScorerMetricContribution(
                    metric_id=metric.id,
                    weight=metric.weight,
                    score=score,
                    weighted_score=round(weighted, 6),
                )
            )
        scorer_score = weighted_score / total_weight if total_weight > 0 else 0.0
        results.append(
            ScorerResult(
                scorer_id=scorer.id,
                version=scorer.version,
                category=scorer.category,
                weight=scorer.weight,
                score=round(scorer_score, 3),
                metric_contributions=contributions,
            )
        )
    return results


def canonical_performance_gates(
    existing: PerformanceGatesScore,
    *,
    quality_score: float,
    min_quality_score: float,
) -> PerformanceGatesScore:
    checks = [
        check.model_copy(deep=True)
        for check in existing.checks
        if check.name != "minimum_quality_score"
    ]
    checks.append(
        GateCheck(
            name="minimum_quality_score",
            passed=quality_score >= min_quality_score,
            evidence=f"quality={quality_score:.3f}, min={min_quality_score:.3f}",
        )
    )
    return PerformanceGatesScore(checks=checks)
