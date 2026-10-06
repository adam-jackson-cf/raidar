"""What analysis reads per trial, and the metrics it compares (F10).

A ``TrialOutcome`` is one arm x attempt x problem trial reduced to
statused values: the trial-level metrics that effects are estimated on
and the per-checkpoint series that curves are drawn from. It is built from
the job's ``TrialRecord`` (validity, arm, attempt) and the Assessment
store (scorer observations), plus the headline metric output. An
unavailable value stays unavailable with its status and reason; it is never
zero (F6), and analysis counts it as an exclusion instead.

Every direction is read from the built-in scorer manifest of the metric it is
computed from, so "improved" means better in the sense the scorer declared
and a manifest change carries through. The one exception is the composite S,
which a Metric defines as higher-better (F15).

``primary_metric`` names the experiment's primary outcome (memo §2.3,
§10.1): the lock's pre-registered ``analysis.primary_outcome`` when it sets
one, else the checkpoint-solve rate (solved / K, the memo's ``C_r``) under
``continue`` progression and the strict ``reached`` count under
``halt_on_failure``. The score component C that the headline metric weighs is
a different quantity (``correctness``: the mean per-checkpoint case pass rate,
the SCB fork's ``C_p``) and is reported as its own metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.scorers.builtin_manifests import CODE_QUALITY, CORRECTNESS, RESOURCE_USAGE
from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort.manifest import GATE_EFFORT
from raidar.scorers.gate_effort_v4.scorer import GATE_EFFORT_V4
from raidar.scorers.process_checks_v3.scorer import PROCESS_COMPLIANCE_V3
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


class TrialOutcome(AnalysisModel):
    trial_id: str
    job_id: str
    arm: str
    attempt: int = Field(ge=0)
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
    """How a metric reads a trial (design memo §10.1)."""

    HEADLINE = "headline"
    CONTINUE = "continue"
    STRICT = "strict"
    QUALITY = "quality"
    GATE = "gate"
    PROCESS = "process"


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
CORRECTNESS_METRIC = "correctness"
SOLVE_RATE = "solve_rate"
REACHED = "reached"
FINAL_SOLVED = "final_solved"
# Errata-applied solve outcomes (``correctness@1`` ``*_errata`` metrics): the same rules
# over the cases the problem's pinned errata keeps. Primary only when the experiment
# pre-registers one (``analysis.primary_outcome``), and then always reported beside its
# raw counterpart (``RAW_COUNTERPART``); ``not_applicable`` for a problem without an errata.
SOLVE_RATE_ERRATA = "solve_rate_errata"
REACHED_ERRATA = "reached_errata"
FINAL_SOLVED_ERRATA = "final_solved_errata"
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
        "Composite score S (headline metric, 0-100)",
        View.HEADLINE,
        SCORE_POLICY_DIRECTION,
        5.0,
    ),
    MetricDef(
        CORRECTNESS_METRIC,
        "Correctness C (mean case pass rate, headline metric input)",
        View.HEADLINE,
        declared_direction(CORRECTNESS, "strict_pass_rate"),
        0.05,
    ),
    MetricDef(
        SOLVE_RATE,
        "Checkpoint solve rate (solved / K)",
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
    MetricDef(
        SOLVE_RATE_ERRATA,
        "Checkpoint solve rate under errata (errata-applied, not the raw SCB grade)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_checkpoints_errata"),
        0.125,
    ),
    MetricDef(
        REACHED_ERRATA,
        "Checkpoints reached under errata (errata-applied, strict)",
        View.STRICT,
        declared_direction(CORRECTNESS, REACHED_ERRATA),
        1.0,
    ),
    MetricDef(
        FINAL_SOLVED_ERRATA,
        "Final checkpoint solved under errata (errata-applied)",
        View.CONTINUE,
        declared_direction(CORRECTNESS, "solved_errata"),
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

GATE_PREFIX = "gate."
"""Gate-effort outcomes are ``gate.<gate-effort trajectory metric>``."""
_GATE_OUTCOMES: tuple[tuple[str, str, float], ...] = (
    ("first_pass_clean_rate", "first-pass clean rate (decided steps)", 0.1),
    ("clean_at_step_end_rate", "clean at step end rate (decided steps)", 0.1),
    ("unresolved_rate", "unresolved rate (fix rounds exhausted)", 0.1),
    ("gate_error_rate", "gate error rate (validity: expected 0)", 0.05),
    ("mean_rounds_to_clean", "mean fix rounds to clean (clean steps)", 0.5),
    ("total_fix_rounds", "fix rounds per trial", 1.0),
    ("total_first_run_blocking", "first-run blocking findings per trial", 5.0),
    *(
        (f"total_first_run_blocking.{family}", f"first-run blocking findings: {family}", 2.0)
        for family in FAMILIES
    ),
    ("total_blocking_findings", "blocking findings over every gate run per trial", 5.0),
    *(
        (f"total_blocking_findings.{family}", f"blocking findings over every run: {family}", 2.0)
        for family in FAMILIES
    ),
    ("total_fix_input_tokens", "fix-session input tokens per trial", 10_000.0),
    ("total_fix_output_tokens", "fix-session output tokens per trial", 1_000.0),
    ("total_fix_cost_usd", "fix-session cost per trial (USD)", 0.01),
    ("total_fix_wall_s", "fix-session wall time per trial (s)", 60.0),
    ("total_gate_wall_s", "gate-run wall time per trial (s)", 5.0),
    # gate-effort@4: the gate's tests check and the fix rounds' correctness.
    ("first_run_tests_pass_rate", "first-run tests check pass rate (decided steps)", 0.1),
    ("tests_missing_rate", "first runs refused for missing tests (decided steps)", 0.1),
    ("tests_pass_at_step_end_rate", "tests check passed at step end (decided steps)", 0.1),
    ("mean_first_run_coverage", "owned line coverage of the first run (percent)", 5.0),
    ("mean_coverage_at_step_end", "owned line coverage at step end (percent)", 5.0),
    (
        "fix_round_correctness_delta",
        "fix-round correctness delta: final minus pre-fix case pass rate",
        0.05,
    ),
)
GATE_METRICS: tuple[MetricDef, ...] = tuple(
    MetricDef(
        f"{GATE_PREFIX}{metric}",
        f"Gate effort: {label}",
        View.GATE,
        declared_direction(GATE_EFFORT_V4, metric),
        min_effect,
    )
    for metric, label, min_effect in _GATE_OUTCOMES
)
"""Gate-effort outcomes: compared only when the lock declares a gate-effort scorer."""
_GATE_V4_ONLY = frozenset(
    f"{GATE_PREFIX}{metric.id}"
    for metric in GATE_EFFORT_V4.metrics
    if metric.id not in {m.id for m in GATE_EFFORT.metrics}
)


def gate_metrics_for(ref: str | None) -> tuple[MetricDef, ...]:
    """The gate outcomes a gate-effort version reports (``@4`` and ``@5`` add the tests check)."""
    if ref in {"gate-effort@4", "gate-effort@5"}:
        return GATE_METRICS
    return tuple(metric for metric in GATE_METRICS if metric.id not in _GATE_V4_ONLY)


PROCESS_PREFIX = "process."
"""Process outcomes are ``process.<process-compliance@3 trajectory metric>``."""
PROCESS_METRICS: tuple[MetricDef, ...] = (
    MetricDef(
        f"{PROCESS_PREFIX}stall_rate",
        "Process: stall rate (steps in which a session stopped to ask, changing nothing)",
        View.PROCESS,
        declared_direction(PROCESS_COMPLIANCE_V3, "stall_rate"),
        0.1,
    ),
)
"""Process outcomes (``process-compliance@3``): compared only when the lock declares it."""
PROCESS_OUTCOME_SCORER = "process-compliance@3"
ALL_METRICS = (*OUTCOME_METRICS, *GATE_METRICS, *PROCESS_METRICS)

TOTAL_COST = "total_cost_usd"
COST_METRICS = ("total_cost_usd", "total_tokens", "total_turns", "total_agent_wall_s")


RAW_COUNTERPART = {
    SOLVE_RATE_ERRATA: SOLVE_RATE,
    REACHED_ERRATA: REACHED,
    FINAL_SOLVED_ERRATA: FINAL_SOLVED,
}
"""The raw SCB outcome an errata-applied primary is always reported beside."""
PRIMARY_FROM_LOCK = "lock (analysis.primary_outcome)"
PRIMARY_DEFAULT = "default for {progression} progression"


def primary_metric(progression: str, configured: str | None = None) -> str:
    """The primary outcome: the pre-registered one, else the progression's (memo §2.3, §10.1)."""
    if configured is not None:
        return outcome_metric(configured).id
    if progression == "continue":
        return SOLVE_RATE
    if progression == "halt_on_failure":
        return REACHED
    raise ValueError(f"no primary outcome is defined for progression {progression!r}")


def primary_source(progression: str, configured: str | None = None) -> str:
    """Where the primary outcome comes from, for the report's label."""
    return PRIMARY_FROM_LOCK if configured else PRIMARY_DEFAULT.format(progression=progression)


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
        return next(metric for metric in ALL_METRICS if metric.id == metric_id)
    except StopIteration as exc:
        known = ", ".join(metric.id for metric in ALL_METRICS)
        raise ValueError(f"unknown outcome metric {metric_id!r}; known: {known}") from exc
