"""Reusable scorer definition loading and scenario resolution."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.schemas.scenario import (
    ArtifactCheckMetricConfig,
    ArtifactCheckMetricDefinition,
    CoreMetricDefinition,
    LLMAsJudgeMetricConfig,
    LLMAsJudgeMetricDefinition,
    MetricDefinition,
    ScorerMetricDefinition,
)
from raidar.scorers.paths import resolve_scorer_definition_file, scorer_definitions_dir


class ScorerResolutionError(ValueError):
    """Raised when a scenario references an invalid scorer definition."""


class ScorerDefinition(BaseModel):
    """Reusable scorer definition loaded from package YAML."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = Field(ge=1)
    status: Literal["active", "proposed"] = "active"
    category: Literal["quality", "efficiency"] = "quality"
    description: str
    metrics: list[ScorerMetricDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_metric_weights(self) -> ScorerDefinition:
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("scorer definition contains duplicate metric ids")
        if sum(metric.weight for metric in self.metrics) <= 0:
            raise ValueError("scorer definition metric weights require positive total")
        return self


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

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def _definition_dir() -> Path:
    return scorer_definitions_dir()


@cache
def load_scorer_definition(scorer_id: str, version: int) -> ScorerDefinition:
    """Load one scorer definition by id/version."""
    path = _definition_dir() / f"{scorer_id}.yaml"
    if not path.exists():
        raise ScorerResolutionError(f"Unknown scorer definition: {scorer_id}@{version}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ScorerResolutionError(f"Invalid scorer definition content: {path}")
    definition = ScorerDefinition.model_validate(payload)
    if definition.version != version:
        raise ScorerResolutionError(
            f"Scorer {scorer_id} requested version {version}, "
            f"but definition version is {definition.version}"
        )
    return definition


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
    """Return first-seen de-duplicated metric definitions for a scenario."""
    by_id: dict[str, MetricDefinition] = {}
    for scorer in resolve_scorers(scenario):
        for metric in scorer.metrics:
            by_id.setdefault(metric.id, _metric_definition(metric))
    return list(by_id.values())


def scorer_evaluation_profile(scenario) -> str:
    """Return deterministic scorer-based evaluation identity."""
    parts = [f"{scorer.id}@{scorer.version}:{scorer.weight:g}" for scorer in scenario.scorers]
    return "scorers:" + "+".join(parts)


def scenario_scorers(scenario) -> list[str]:
    """Return deterministic scorer references."""
    return [f"{scorer.id}@{scorer.version}" for scorer in scenario.scorers]
