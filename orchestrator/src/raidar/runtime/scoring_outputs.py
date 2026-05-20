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
from raidar.scoring.acceptance import evaluate_llm_as_judge_metric


def build_metric_scores(
    context: ScorecardBuildContext,
    *,
    execution_validity: ExecutionValidityScore,
    resource_efficiency: ResourceEfficiencyScore,
) -> list[MetricScore]:
    outputs = context.execution.outputs
    metric_scores: dict[str, MetricScore] = {
        metric.metric_id: metric.model_copy(deep=True) for metric in outputs.metric_scores
    }
    core_scores = _core_metric_scores(outputs, execution_validity, resource_efficiency)
    for metric in context.request.scenario.resolved_metrics():
        metric_id = metric.id
        if metric_id in core_scores:
            metric_scores[metric_id] = core_scores[metric_id]
        elif metric.type == "llm-as-judge":
            metric_scores[metric_id] = evaluate_llm_as_judge_metric(
                workspace=context.context.workspace,
                scenario_dir=context.request.scenario_dir,
                scenario=context.request.scenario,
                metric_id=metric.id,
                judge_path=metric.config.judge,
            )
        elif metric_id not in metric_scores:
            metric_scores[metric_id] = MetricScore(
                metric_id=metric_id,
                score=0.0,
                passed=False,
                evidence="Metric was resolved but no evaluator output was produced.",
            )
    return [metric_scores[metric.id] for metric in context.request.scenario.resolved_metrics()]


def _core_metric_scores(
    outputs,
    execution_validity: ExecutionValidityScore,
    resource_efficiency: ResourceEfficiencyScore,
) -> dict[str, MetricScore]:
    return {
        "functional": _functional_metric_score(outputs),
        "acceptance": _acceptance_metric_score(outputs),
        "verification-stability": _verification_stability_metric_score(outputs),
        "execution-validity": _execution_validity_metric_score(execution_validity),
        "resource-efficiency": _resource_efficiency_metric_score(resource_efficiency),
        "test-coverage": _test_coverage_metric_score(outputs),
        "requirements-coverage": _requirements_coverage_metric_score(outputs),
        "visual-regression": _visual_regression_metric_score(outputs),
    }


def _functional_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="functional",
        score=outputs.functional.score,
        passed=outputs.functional.passed,
        evidence=(
            f"build={outputs.functional.build_succeeded}, "
            f"tests={outputs.functional.tests_passed}/{outputs.functional.tests_total}"
        ),
    )


def _acceptance_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="acceptance",
        score=outputs.acceptance.score,
        passed=outputs.acceptance.score >= 1.0,
        evidence=f"checks={len(outputs.acceptance.checks)}",
    )


def _verification_stability_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="verification-stability",
        score=outputs.verification_stability.score,
        passed=outputs.verification_stability.score > 0,
        evidence=f"failures={outputs.verification_stability.total_gate_failures}",
    )


def _execution_validity_metric_score(score: ExecutionValidityScore) -> MetricScore:
    return MetricScore(
        metric_id="execution-validity",
        score=1.0 if score.passed else 0.0,
        passed=score.passed,
        evidence=f"checks={len(score.checks)}",
    )


def _resource_efficiency_metric_score(score: ResourceEfficiencyScore) -> MetricScore:
    return MetricScore(
        metric_id="resource-efficiency",
        score=score.score,
        passed=True,
        evidence=(
            f"uncached_input_tokens={score.uncached_input_tokens}, "
            f"command_count={score.command_count}"
        ),
    )


def _test_coverage_metric_score(outputs) -> MetricScore:
    return MetricScore(
        metric_id="test-coverage",
        score=_coverage_metric_score(outputs.test_coverage),
        passed=outputs.test_coverage.passed,
        evidence=(
            f"threshold={outputs.test_coverage.threshold}, "
            f"measured={outputs.test_coverage.measured}, "
            f"source={outputs.test_coverage.source}"
        ),
    )


def _requirements_coverage_metric_score(outputs) -> MetricScore:
    score = outputs.requirements_coverage
    return MetricScore(
        metric_id="requirements-coverage",
        score=_requirements_metric_score(score),
        passed=score.presence_ratio >= 1.0 and score.mapping_ratio >= 1.0,
        evidence=f"presence={score.presence_ratio}, mapping={score.mapping_ratio}",
    )


def _visual_regression_metric_score(outputs) -> MetricScore:
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
        passed=outputs.visual.passed,
        evidence=f"similarity={outputs.visual.score}, passed={outputs.visual.passed}",
    )


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


def _coverage_metric_score(score) -> float:
    if score.threshold is None:
        return 1.0 if score.passed else 0.0
    if score.measured is None:
        return 0.0
    return min(1.0, score.measured / score.threshold)


def _requirements_metric_score(score) -> float:
    return score.presence_ratio * 0.5 + score.mapping_ratio * 0.5
