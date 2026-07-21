"""Code-backed scorer definition registry and scenario resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib import import_module
from typing import Any

from raidar.schemas.scenario import (
    ArtifactCheckMetricConfig,
    ArtifactCheckMetricDefinition,
    CapabilityRequirements,
    CoreMetricDefinition,
    LLMAsJudgeMetricConfig,
    LLMAsJudgeMetricDefinition,
    MetricDefinition,
    ScorerMetricDefinition,
)
from raidar.scorers.base import (
    ScorerDefinition,
    ScorerResolutionError,
    scorer_class,
)
from raidar.scorers.paths import resolve_scorer_definition_file
from raidar.scorers.verification import EvidenceDependency

import_module("raidar.scorers.scorer_registration")


@dataclass(frozen=True, slots=True)
class ResolvedScorer:
    """Scorer definition with scenario-level weight and merged metric config."""

    id: str
    version: int
    status: str
    category: str
    description: str
    weight: float
    metrics: tuple[ScorerMetricDefinition, ...]
    extends: str | None = None
    requirements: CapabilityRequirements = field(default_factory=CapabilityRequirements)
    evidence_dependencies: tuple[EvidenceDependency, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@cache
def load_scorer_definition(scorer_id: str, version: int) -> ScorerDefinition:
    """Load one code-backed scorer definition by id/version."""

    return scorer_class(scorer_id, version).definition()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _merge_metric_config(
    metric: ScorerMetricDefinition, scenario_config: dict[str, dict[str, Any]]
) -> ScorerMetricDefinition:
    override = scenario_config.get(metric.id, {})
    if not override:
        return metric
    if metric.type == "llm-as-judge":
        raise ScorerResolutionError(
            "llm-as-judge metric config is owned by scorer definitions "
            f"and cannot be overridden by scenarios: {metric.id}"
        )
    return metric.model_copy(update={"config": _deep_merge(metric.config, override)})


def resolve_scorers(scenario) -> list[ResolvedScorer]:
    """Resolve attached scorer references for a scenario."""
    resolved: list[ResolvedScorer] = []
    for scorer_ref in scenario.scorers:
        definition = load_scorer_definition(scorer_ref.id, scorer_ref.version)
        if definition.status != "active":
            raise ScorerResolutionError(
                f"Scorer {definition.id}@{definition.version} is {definition.status} "
                "and cannot be attached to a scenario"
            )
        metric_ids = {metric.id for metric in definition.metrics}
        unknown_config_keys = sorted(set(scorer_ref.config) - metric_ids)
        if unknown_config_keys:
            raise ScorerResolutionError(
                f"Scorer {definition.id}@{definition.version} config references "
                "metrics not in scorer definition: " + ", ".join(unknown_config_keys)
            )
        metrics = tuple(
            _merge_metric_config(metric, scorer_ref.config) for metric in definition.metrics
        )
        resolved.append(
            ResolvedScorer(
                id=definition.id,
                version=definition.version,
                status=definition.status,
                category=definition.category,
                description=definition.description,
                weight=scorer_ref.weight,
                metrics=metrics,
                extends=definition.extends,
                requirements=definition.requirements,
                evidence_dependencies=tuple(definition.evidence_dependencies),
            )
        )
    if sum(scorer.weight for scorer in resolved) <= 0:
        raise ScorerResolutionError("scenario scorer weights require positive total")
    return resolved


def _metric_definition(metric: ScorerMetricDefinition) -> MetricDefinition:
    if metric.type == "core":
        return CoreMetricDefinition(id=metric.id)
    if metric.type == "artifact-checks":
        config = ArtifactCheckMetricConfig.model_validate(metric.config)
        return ArtifactCheckMetricDefinition(config=config)
    if metric.type == "llm-as-judge":
        config = LLMAsJudgeMetricConfig.model_validate(metric.config)
        resolve_scorer_definition_file(
            config.judge,
            field_name=f"{metric.id}.config.judge",
        )
        return LLMAsJudgeMetricDefinition(id=metric.id, config=config)
    raise ScorerResolutionError(f"Unsupported scorer metric type: {metric.type}")


def resolved_metrics(scenario) -> list[MetricDefinition]:
    """Return metric definitions with exactly one owning scorer."""

    metrics: list[MetricDefinition] = []
    owners: dict[str, str] = {}
    for scorer in resolve_scorers(scenario):
        for metric in scorer.metrics:
            owner = owners.get(metric.id)
            if owner is not None:
                raise ScorerResolutionError(
                    f"Metric {metric.id!r} is owned by both {owner} and {scorer.ref}"
                )
            owners[metric.id] = scorer.ref
            metrics.append(_metric_definition(metric))
    return metrics


def scorer_evaluation_profile(scenario) -> str:
    """Return deterministic scorer-based evaluation identity."""
    parts = [f"{scorer.id}@{scorer.version}:{scorer.weight:g}" for scorer in scenario.scorers]
    return "scorers:" + "+".join(parts)


def scenario_scorers(scenario) -> list[str]:
    """Return deterministic scorer references."""
    return [f"{scorer.id}@{scorer.version}" for scorer in scenario.scorers]
