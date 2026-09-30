"""What analysis reads per trajectory, and the metrics it compares (F10).

A ``TrajectoryOutcome`` is one arm x block x problem trajectory reduced to
statused values: the trajectory-level metrics that effects are estimated on
and the per-checkpoint series that curves are drawn from. It is built from
the run's ``TrajectoryRecord`` (validity, arm, block) and the Assessment
store (scorer observations), plus the headline ScorePolicy output. An
unavailable value stays unavailable with its status and reason; it is never
zero (F6), and analysis counts it as an exclusion instead.

Every direction is read from the built-in scorer manifest of the metric it is
computed from, so "improved" means better in the sense the scorer declared
and a manifest change carries through. The one exception is the composite S,
which a ScorePolicy defines as higher-better (F15).

``primary_metric`` names the experiment's primary outcome (memo §2.3,
§10.1): the checkpoint-solve rate C under ``continue`` progression and the
strict ``reached`` count under ``halt_on_failure``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.scorers.builtin_manifests import CODE_QUALITY, CORRECTNESS, RESOURCE_USAGE
from raidar.scorers.sdk.manifest import Direction, ScorerManifest

OK = "ok"
UNASSESSED = "unassessed"
TRUNCATED = "truncated"


class AnalysisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Value(AnalysisModel):
    """A measured value (status ``ok``) or why there is none."""

    status: str = Field(min_length=1)
    value: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if (self.status == OK) != (self.value is not None):
            raise ValueError("a value is present exactly when the status is ok")
        if self.status != OK and not self.reason:
            raise ValueError(f"a {self.status} value needs a reason")
        return self

    @classmethod
    def ok(cls, value: float) -> Value:
        return cls(status=OK, value=value)

    @classmethod
    def missing(cls, status: str, reason: str) -> Value:
        return cls(status=status, reason=reason)

    @property
    def measured(self) -> bool:
        return self.status == OK


class TrajectoryOutcome(AnalysisModel):
    trial_id: str
    run_id: str
    arm: str
    block: int = Field(ge=0)
    problem: str
    steps: tuple[str, ...] = Field(min_length=1)
    infra_error: bool
    infra_reasons: tuple[str, ...] = ()
    metrics: dict[str, Value] = Field(default_factory=dict)
    series: dict[str, tuple[Value, ...]] = Field(default_factory=dict)

    def metric(self, metric_id: str) -> Value:
        return self.metrics.get(metric_id) or Value.missing(
            UNASSESSED, f"{metric_id} was not assessed"
        )

    def point(self, series_id: str, index: int) -> Value:
        values = self.series.get(series_id, ())
        if index < len(values):
            return values[index]
        step = self.steps[index] if index < len(self.steps) else f"#{index}"
        return Value.missing(UNASSESSED, f"{series_id}[{step}] was not assessed")


class View(StrEnum):
    """How a metric reads a trajectory (design memo §10.1)."""

    HEADLINE = "headline"
    CONTINUE = "continue"
    STRICT = "strict"
    QUALITY = "quality"


class Kind(StrEnum):
    CONTINUOUS = "continuous"
    BINARY = "binary"


@dataclass(frozen=True)
class MetricDef:
    """A compared outcome. ``min_effect`` is the default δ for the sample-size projection."""

    id: str
    label: str
    view: View
    direction: Direction
    min_effect: float
    kind: Kind = Kind.CONTINUOUS


def declared_direction(manifest: ScorerManifest, metric_id: str) -> Direction:
    """The direction ``manifest`` declares for ``metric_id``; unknown metrics are refused."""
    metric = manifest.metric(metric_id)
    if metric is None:
        raise ValueError(f"{manifest.ref} declares no metric {metric_id!r}")
    return metric.direction


HEADLINE_SCORE = "headline_score"
SOLVE_RATE = "solve_rate"
REACHED = "reached"
FINAL_SOLVED = "final_solved"
QUALITY_COMPONENTS = {
    "verbosity": "V",
    "erosion": "E",
    "architecture": "H",
    "rework": "R",
    "regression": "G",
}
SCORE_POLICY_DIRECTION = Direction.HIGHER_BETTER

OUTCOME_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        HEADLINE_SCORE,
        "Composite score S (headline ScorePolicy, 0-100)",
        View.HEADLINE,
        SCORE_POLICY_DIRECTION,
        5.0,
    ),
    MetricDef(
        SOLVE_RATE,
        "Checkpoint solve rate C",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_checkpoints"),
        0.125,
    ),
    MetricDef(
        REACHED,
        "Checkpoints reached (strict)",
        View.STRICT,
        declared_direction(CORRECTNESS, REACHED),
        1.0,
    ),
    MetricDef(
        FINAL_SOLVED,
        "Final checkpoint solved",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved"),
        0.25,
        Kind.BINARY,
    ),
    *(
        MetricDef(
            name,
            f"Quality {component} ({name})",
            View.QUALITY,
            declared_direction(CODE_QUALITY, f"problem_{name}"),
            0.05,
        )
        for name, component in QUALITY_COMPONENTS.items()
    ),
)

TOTAL_COST = "total_cost_usd"
COST_METRICS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")


def primary_metric(progression: str) -> str:
    """The primary outcome for a progression mode (memo §2.3 and §10.1)."""
    if progression == "continue":
        return SOLVE_RATE
    if progression == "halt_on_failure":
        return REACHED
    raise ValueError(f"no primary outcome is defined for progression {progression!r}")


@dataclass(frozen=True)
class SeriesDef:
    id: str
    label: str
    view: View
    direction: Direction


SOLVED = "solved"
STRICT_SOLVED = "strict_solved"


def _series(series_id: str, label: str, view: View, manifest: ScorerManifest) -> SeriesDef:
    return SeriesDef(series_id, label, view, declared_direction(manifest, series_id))


CHECKPOINT_SERIES: tuple[SeriesDef, ...] = (
    _series(SOLVED, "Solved (continue view)", View.CONTINUE, CORRECTNESS),
    SeriesDef(
        STRICT_SOLVED,
        "Solved within the reached prefix (strict view)",
        View.STRICT,
        declared_direction(CORRECTNESS, SOLVED),
    ),
    _series("strict_pass_rate", "Case pass rate", View.CONTINUE, CORRECTNESS),
    _series("verbosity", "Verbosity V", View.QUALITY, CODE_QUALITY),
    _series("erosion", "Erosion E", View.QUALITY, CODE_QUALITY),
    _series("architecture", "Architecture H", View.QUALITY, CODE_QUALITY),
    _series("cost_usd", "Cost per checkpoint (USD)", View.CONTINUE, RESOURCE_USAGE),
)


def outcome_metric(metric_id: str) -> MetricDef:
    try:
        return next(metric for metric in OUTCOME_METRICS if metric.id == metric_id)
    except StopIteration as exc:
        known = ", ".join(metric.id for metric in OUTCOME_METRICS)
        raise ValueError(f"unknown outcome metric {metric_id!r}; known: {known}") from exc
