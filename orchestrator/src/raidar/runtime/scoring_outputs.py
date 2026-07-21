"""Canonical metric and scorer output assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from raidar.runtime.models import ScorecardBuildContext
from raidar.schemas.scenario import ScenarioDefinition, ScenarioScorerRef
from raidar.schemas.scorecard import (
    ExecutionValidityScore,
    GateCheck,
    MetricScore,
    PerformanceGatesScore,
    ResourceEfficiencyScore,
    ScorerMetricContribution,
    ScorerResult,
)
from raidar.scorers.base import ScorerContext, ScorerEvidence, scorer_class
from raidar.scorers.evidence_contract import EvidenceOutput
from raidar.scorers.llm_as_judge import codex_judge_executor
from raidar.scorers.verification import (
    VerificationEvidence,
    VerificationEvidenceError,
)


def build_metric_scores(
    context: ScorecardBuildContext,
    *,
    execution_validity: ExecutionValidityScore,
    resource_efficiency: ResourceEfficiencyScore,
) -> list[MetricScore]:
    judge_image_name = context.execution.cache_metadata.get("judge_image_name")
    scorer_context = ScorerContext(
        workspace=context.context.workspace,
        scenario_dir=context.request.scenario_dir,
        scenario=context.request.scenario,
        execution=context.execution,
        resource_efficiency=resource_efficiency,
        execution_validity=execution_validity,
        verification_evidence=_load_verification_evidence(context),
        workspace_changes=context.artifacts.workspace_changes,
        retained_evidence=context.artifacts.evidence_artifacts,
        judge_executor=(
            codex_judge_executor(judge_image_name)
            if isinstance(judge_image_name, str) and judge_image_name
            else None
        ),
    )
    return collect_metric_scores_from_scorers(context.request.scenario, scorer_context)


class ScorerEvidenceAdapter(Protocol):
    """Typed evidence collection boundary shared by production and fixtures."""

    def collect_evidence(
        self,
        scorer_ref: ScenarioScorerRef,
        context: ScorerContext,
    ) -> ScorerEvidence: ...


class RegisteredScorerEvidenceAdapter:
    """Collect evidence from the registered executable scorer implementation."""

    def collect_evidence(
        self,
        scorer_ref: ScenarioScorerRef,
        context: ScorerContext,
    ) -> ScorerEvidence:
        scorer = scorer_class(scorer_ref.id, scorer_ref.version)()
        return scorer.collect_evidence(context)


def _validated_owned_metric_scores(
    evidence: ScorerEvidence,
    *,
    owner_ref: str,
    owned_metric_ids: set[str],
) -> tuple[MetricScore, ...]:
    """Normalize one scorer's outputs and reject metrics it does not own."""

    scores: list[MetricScore] = []
    for metric_score in evidence.metric_scores:
        score = MetricScore.model_validate(metric_score)
        if score.metric_id not in owned_metric_ids:
            raise ValueError(f"Scorer {owner_ref} emitted unowned metric {score.metric_id!r}")
        scores.append(score)
    return tuple(scores)


def _merge_unique_metric_scores(
    metric_scores: dict[str, MetricScore],
    scorer_scores: tuple[MetricScore, ...],
) -> None:
    """Merge scorer outputs while rejecting duplicate metric ownership."""

    for score in scorer_scores:
        if score.metric_id in metric_scores:
            raise ValueError(f"Duplicate scorer metric output: {score.metric_id}")
        metric_scores[score.metric_id] = score


def _ordered_complete_metric_scores(
    metric_scores: dict[str, MetricScore],
    resolved_metric_ids: list[str],
) -> list[MetricScore]:
    """Return resolved order only when every required metric was emitted."""

    missing_metric_ids = [
        metric_id for metric_id in resolved_metric_ids if metric_id not in metric_scores
    ]
    if missing_metric_ids:
        raise ValueError(
            "Scorers did not emit all resolved metrics: " + ", ".join(missing_metric_ids)
        )
    return [metric_scores[metric_id] for metric_id in resolved_metric_ids]


def collect_metric_scores_from_scorers(
    scenario: ScenarioDefinition,
    context: ScorerContext,
    adapter: ScorerEvidenceAdapter | None = None,
) -> list[MetricScore]:
    """Collect, validate, and order scorer-owned metrics for one scenario."""

    evidence_adapter = adapter or RegisteredScorerEvidenceAdapter()
    metric_scores: dict[str, MetricScore] = {}
    resolved_scorers = scenario.resolved_scorers()
    for scorer_ref, resolved in zip(scenario.scorers, resolved_scorers, strict=True):
        evidence = evidence_adapter.collect_evidence(scorer_ref, context)
        owned_metric_ids = {metric.id for metric in resolved.metrics}
        _merge_unique_metric_scores(
            metric_scores,
            _validated_owned_metric_scores(
                evidence,
                owner_ref=resolved.ref,
                owned_metric_ids=owned_metric_ids,
            ),
        )
    resolved_metric_ids = [metric.id for metric in scenario.resolved_metrics()]
    return _ordered_complete_metric_scores(metric_scores, resolved_metric_ids)


def _load_verification_evidence(context: ScorecardBuildContext) -> VerificationEvidence:
    evidence_path = context.artifacts.runner_evidence_artifacts.get("evidence.json")
    if not evidence_path:
        return VerificationEvidence()
    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        output = EvidenceOutput.model_validate(payload)
        return VerificationEvidence.from_output(output)
    except (OSError, json.JSONDecodeError, ValidationError, VerificationEvidenceError) as exc:
        raise VerificationEvidenceError(
            f"invalid current-version verification evidence at {evidence_path}: {exc}"
        ) from exc


def build_scorer_results(
    context: ScorecardBuildContext,
    metric_scores: list[MetricScore],
) -> list[ScorerResult]:
    return build_scorer_results_for_scenario(context.request.scenario, metric_scores)


def build_scorer_results_for_scenario(
    scenario: ScenarioDefinition,
    metric_scores: list[MetricScore],
) -> list[ScorerResult]:
    """Assemble scorer results from resolved scenario definitions and metric evidence."""

    score_by_id = {metric.metric_id: metric.score for metric in metric_scores}
    results: list[ScorerResult] = []
    for scorer in scenario.resolved_scorers():
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
