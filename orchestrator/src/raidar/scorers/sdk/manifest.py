"""Static, versioned scorer manifests (design memo §3.2, plan F1).

A manifest declares everything a scorer may emit before it runs: each metric's
level, type, direction, measurement tier and SCB exactness. Scorers observe
only; nothing here carries a weight or a success rule (F6). Weights live in
ScorePolicies (``raidar.spec.policies``).
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.packs.python.models import ToolExecution
from raidar.spec.canonical import digest_json

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
_METRIC_PATTERN = r"^[a-z0-9][a-z0-9_]*(\.[a-z0-9][a-z0-9_]*)*$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]*$"
ANY_RUNTIME = "*"


class SdkModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MetricLevel(StrEnum):
    """What one observation describes: a checkpoint, a transition or a whole trajectory."""

    CHECKPOINT = "checkpoint"
    TRANSITION = "transition"
    TRAJECTORY = "trajectory"


class MetricType(StrEnum):
    RATIO = "ratio"
    PROBABILITY = "probability"
    COUNT = "count"
    BOOLEAN = "boolean"
    ORDINAL = "ordinal"
    REAL = "real"


class Direction(StrEnum):
    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"
    NEUTRAL = "neutral"


class Tier(StrEnum):
    """Evidence ladder (design memo §3.4). Only ``deterministic`` may be authoritative."""

    DETERMINISTIC = "deterministic"
    JEV = "jev"
    LLM = "llm"


class Exactness(StrEnum):
    SCB_EXACT = "scb-exact"
    SCB_INSPIRED = "scb-inspired"
    NATIVE = "native"


class EvidenceInput(StrEnum):
    """Run inputs a scorer reads; they feed the Assessment key."""

    SNAPSHOT_CURRENT = "snapshot.current"
    SNAPSHOT_PRIOR = "snapshot.prior"
    SNAPSHOT_STARTER = "snapshot.starter"
    ORACLE_CASES = "oracle.cases"
    TRACE = "trace"


_UNIT_TYPES = frozenset({MetricType.RATIO, MetricType.PROBABILITY})


class MetricSpec(SdkModel):
    """One declared metric. ``id`` is local; ``<scorer>.<id>`` is its global name.

    ``inputs`` narrows which of the scorer's evidence inputs this metric reads
    (empty means all of them). It decides, for example, whether an invalid
    snapshot inventory makes the metric ineligible for that checkpoint.
    """

    id: str = Field(pattern=_METRIC_PATTERN)
    description: str = Field(min_length=1)
    level: MetricLevel
    type: MetricType
    direction: Direction
    tier: Tier
    exactness: Exactness
    unit: str | None = None
    range: tuple[float, float] | None = None
    inputs: tuple[EvidenceInput, ...] = ()

    @model_validator(mode="after")
    def _range_fits_type(self) -> Self:
        if self.range is not None:
            low, high = self.range
            if not (math.isfinite(low) and math.isfinite(high)) or low > high:
                raise ValueError(f"metric {self.id}: range must be finite with low <= high")
        if self.type in _UNIT_TYPES and self.range not in (None, (0.0, 1.0)):
            raise ValueError(f"metric {self.id}: {self.type.value} values lie in [0, 1]")
        return self

    def bounds(self) -> tuple[float, float] | None:
        """The closed interval an ``ok`` value must lie in, if any."""
        if self.type in _UNIT_TYPES or self.type is MetricType.BOOLEAN:
            return (0.0, 1.0)
        if self.type is MetricType.COUNT and self.range is None:
            return (0.0, math.inf)
        return self.range

    def admits(self, value: float) -> str | None:
        """Why ``value`` is not a valid ``ok`` observation of this metric, or None."""
        if not math.isfinite(value):
            return "value must be finite"
        integral = self.type in {MetricType.COUNT, MetricType.BOOLEAN, MetricType.ORDINAL}
        if integral and not float(value).is_integer():
            return f"{self.type.value} value must be integral"
        bounds = self.bounds()
        if bounds is not None and not bounds[0] <= value <= bounds[1]:
            return f"value {value} outside {list(bounds)}"
        return None


def _reject_duplicates(scorer: str, label: str, values: list[str]) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"scorer {scorer}: duplicate {label} {duplicates}")


class ScorerManifest(SdkModel):
    """A scorer's static declaration, addressed as ``id@version``."""

    id: str = Field(pattern=_ID_PATTERN)
    version: str = Field(pattern=_VERSION_PATTERN)
    description: str = Field(min_length=1)
    runtimes: tuple[str, ...] = Field(min_length=1)
    evidence_required: tuple[EvidenceInput, ...] = Field(min_length=1)
    tools: tuple[str, ...] = ()
    execution: ToolExecution = ToolExecution.HOST_PARSE
    metrics: tuple[MetricSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_declarations(self) -> Self:
        for label, values in (
            ("metric", [metric.id for metric in self.metrics]),
            ("runtime", list(self.runtimes)),
            ("tool", list(self.tools)),
            ("evidence input", [item.value for item in self.evidence_required]),
        ):
            _reject_duplicates(self.id, label, values)
        if ANY_RUNTIME in self.runtimes and len(self.runtimes) > 1:
            raise ValueError(f"scorer {self.id}: runtime '*' cannot be combined with others")
        if ANY_RUNTIME in self.runtimes and self.tools:
            raise ValueError(f"scorer {self.id}: runtime-neutral scorers cannot pin pack tools")
        for metric in self.metrics:
            undeclared = sorted(set(metric.inputs) - set(self.evidence_required))
            if undeclared:
                raise ValueError(
                    f"scorer {self.id}: metric {metric.id} reads undeclared inputs {undeclared}"
                )
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def metric_inputs(self, metric: MetricSpec) -> frozenset[EvidenceInput]:
        return frozenset(metric.inputs or self.evidence_required)

    def metric(self, metric_id: str) -> MetricSpec | None:
        return next((metric for metric in self.metrics if metric.id == metric_id), None)

    def qualified(self, metric_id: str) -> str:
        """Global metric name; scorer ids namespace metrics, so namespaces are disjoint."""
        return f"{self.id}.{metric_id}"

    def definition(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def digest(self) -> str:
        return digest_json(self.definition())
