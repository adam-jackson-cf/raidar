"""Code-backed scorer definitions and registration primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.schemas.scenario import ScorerMetricDefinition


class ScorerResolutionError(ValueError):
    """Raised when a scenario references an invalid scorer definition."""


class ScorerDefinition(BaseModel):
    """Reusable scorer definition exposed by a registered scorer implementation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = Field(ge=1)
    status: Literal["active", "proposed"] = "active"
    category: Literal["quality", "efficiency"] = "quality"
    description: str
    metrics: list[ScorerMetricDefinition] = Field(min_length=1)
    extends: str | None = None
    runtime: str | None = None

    @model_validator(mode="after")
    def _validate_metric_weights(self) -> ScorerDefinition:
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("scorer definition contains duplicate metric ids")
        if sum(metric.weight for metric in self.metrics) <= 0:
            raise ValueError("scorer definition metric weights require positive total")
        return self


@dataclass(frozen=True, slots=True)
class ScorerContext:
    """Runtime context provided to executable scorer implementations."""

    workspace: Any
    scenario_dir: Any
    scenario: Any
    execution: Any
    resource_efficiency: Any
    execution_validity: Any


@dataclass(frozen=True, slots=True)
class ScorerEvidence:
    """Evidence bundle collected by a scorer implementation."""

    metric_scores: tuple[Any, ...] = ()
    metadata: dict[str, Any] | None = None


class BaseScorer:
    """Base class for code-backed scorer implementations."""

    id: ClassVar[str]
    version: ClassVar[int]
    status: ClassVar[Literal["active", "proposed"]] = "active"
    category: ClassVar[Literal["quality", "efficiency"]] = "quality"
    description: ClassVar[str]
    metrics: ClassVar[tuple[ScorerMetricDefinition, ...]]
    extends: ClassVar[str | None] = None
    runtime: ClassVar[str | None] = None

    @classmethod
    def definition(cls) -> ScorerDefinition:
        """Return the public scorer definition for scenario resolution."""

        return ScorerDefinition(
            id=cls.id,
            version=cls.version,
            status=cls.status,
            category=cls.category,
            description=cls.description,
            metrics=list(cls.metrics),
            extends=cls.extends,
            runtime=cls.runtime,
        )

    def collect_evidence(self, context: ScorerContext) -> ScorerEvidence:
        """Collect scorer-owned evidence before scoring.

        Concrete scorer implementations own the runtime/tool-specific evidence
        collection. The initial migration keeps existing scorecard metrics wired
        through the canonical scorecard phase, so default evidence is empty.
        """

        return ScorerEvidence()


_SCORER_REGISTRY: dict[tuple[str, int], type[BaseScorer]] = {}


def register_scorer(*, id: str, version: int):
    """Register a code-backed scorer implementation."""

    def decorator(cls: type[BaseScorer]) -> type[BaseScorer]:
        key = (id, version)
        if key in _SCORER_REGISTRY:
            raise ScorerResolutionError(f"Duplicate scorer registration: {id}@{version}")
        cls.id = id
        cls.version = version
        _SCORER_REGISTRY[key] = cls
        return cls

    return decorator


def scorer_class(scorer_id: str, version: int) -> type[BaseScorer]:
    """Return a registered scorer class by id/version."""

    try:
        return _SCORER_REGISTRY[(scorer_id, version)]
    except KeyError as exc:
        raise ScorerResolutionError(f"Unknown scorer definition: {scorer_id}@{version}") from exc


def registered_scorers() -> dict[tuple[str, int], type[BaseScorer]]:
    """Return registered scorer classes keyed by id/version."""

    return dict(_SCORER_REGISTRY)
