"""What analysis reads per trial (F10).

A ``TrialOutcome`` is one arm x attempt x problem trial reduced to
statused values: the trial-level metrics that effects are estimated on
and the per-checkpoint series that curves are drawn from. It is built from
the job's ``TrialRecord`` (validity, arm, attempt) and the Assessment
store (scorer observations), plus the headline metric output. An
unavailable value stays unavailable with its status and reason; it is never
zero (F6), and analysis counts it as an exclusion instead. The metrics themselves
are catalogued in ``raidar.scoring.catalog.outcomes``.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

OK = "ok"
UNASSESSED = "unassessed"
TRUNCATED = "truncated"


class AnalysisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PoolFinding(AnalysisModel):
    field: str
    message: str


class ParityComparison(AnalysisModel):
    """The parity record ``trials`` valid trials of one problem share (pooling)."""

    trials: int
    record: dict[str, Any]


class PoolingReport(AnalysisModel):
    unmeasured: tuple[str, ...] = ()
    """Jobs without ``mechanics_epoch``: no records manifest, integrity not measured."""
    overrides_used: tuple[str, ...] = ()
    findings: tuple[PoolFinding, ...] = ()
    """The refusals lifted by ``--pool-override`` (every one is recorded)."""
    timing_cost_comparable: bool = True
    """False when the jobs ran on different execution-target hardware: timing and cost
    metrics are then not comparable across them (correctness and quality still pool)."""
    capabilities: dict[str, str] | None = None
    """The declaration every arm shares (``None``: it differs, or only pre-epoch jobs)."""
    varies: tuple[str, ...] = ()
    """The factors the experiment declared it varies (``harness``, ``model``, ``reasoning``)."""
    parity: dict[str, ParityComparison] = Field(default_factory=dict)
    """Per problem, the parity record every valid trial of every arm shares."""


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


class DependencyStep(AnalysisModel):
    """One attempted step's dependency readings (health ``D1``/``D2``); ``None`` is unmeasured."""

    step: str
    unprovided: tuple[dict[str, str], ...] | None = None
    """``{requirement, reason}`` per declared requirement Raidar could not provide."""
    undeclared_imports: tuple[dict[str, str], ...] | None = None
    """``{module, basis}`` per non-stdlib, non-local module the grader log failed to import that
    no declared name matches; ``basis`` is ``confirmed`` or ``heuristic`` (an import name can
    differ from its distribution's name)."""
    solvable_solved: bool | None = None
    """The step's solvable-set verdict; ``None`` when it has none (never read as failed)."""


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
    dependency_steps: tuple[DependencyStep, ...] = ()
    """Per attempted step, the dependency readings; empty when the trial has no health audit."""

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
