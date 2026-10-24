"""The Quality projection contract (``raidar-quality-projection``, schema_version 1).

The review surface's Quality element reads this JSON, which is separate from
the frozen ``schema_version=1`` review projection (design memo §12.3): the
frozen UI and its projection stay untouched, and the Quality route reads
``/api/quality/*`` instead.

Every cell is statused. A value is present only when the status is ``ok``;
otherwise the cell carries the scorer's or policy's reason, never a zero
(F6). Components are the SCB fork's: C (correctness), V, E, H, R, G, the
inertia I and the score S, weighted by the run's headline metric.
"""

from __future__ import annotations

from typing import Literal, Self, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.evaluation.analysis.outcomes import Value

SCHEMA_VERSION = 1
PROJECTION_KIND = "raidar-quality-projection"
COMPONENTS = ("C", "V", "E", "H", "R", "G", "I", "S")
DRILLDOWN_COMPONENTS = ("V", "E", "H", "R", "G")
CellStatus = Literal[
    "ok",
    "not_applicable",
    "ineligible",
    "insufficient_evidence",
    "unsupported",
    "unavailable",
    "error",
    "unassessed",
    "truncated",
]
Verdict = Literal["improved", "worse", "inconclusive", "insufficient"]


def cell_status(value: str) -> CellStatus:
    if value not in get_args(CellStatus):
        raise ValueError(f"unknown assessment status {value!r}")
    return cast(CellStatus, value)


class QualityModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Cell(QualityModel):
    status: CellStatus
    value: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if self.status == "ok" and self.value is None:
            raise ValueError("an ok cell needs a value")
        if self.status != "ok" and (self.value is not None or not self.reason):
            raise ValueError(f"a {self.status} cell carries a reason and no value")
        return self

    @classmethod
    def of_value(cls, value: Value) -> Cell:
        """The cell of an analysis ``Value``: its number, or its status and reason."""
        if value.value is not None:
            return cls(status="ok", value=value.value)
        return cls(status=cell_status(value.status), reason=value.reason)


class PolicyInfo(QualityModel):
    ref: str
    source: Literal["lock", "default_by_k"]
    inertia_weights: dict[str, float]
    not_applicable: tuple[str, ...] = ()


class CheckpointSignals(QualityModel):
    """SCB-dashboard signals at one checkpoint (``None`` when unmeasured)."""

    lint_findings: int | None = None
    production_loc: int | None = None
    lint_per_kloc: float | None = None
    high_cc_count: int | None = None
    high_cc_mass_share: float | None = None
    high_cc_function_lines: tuple[int, ...] = ()


class CheckpointRow(QualityModel):
    checkpoint: str
    state: str
    cells: dict[str, Cell]
    signals: CheckpointSignals


class EvidenceItem(QualityModel):
    component: str
    subject: str
    checkpoint: str
    path: str
    lines: tuple[tuple[int, int], ...] = Field(min_length=1)
    kind: str
    message: str
    sha256: str
    snapshot_path: str | None = None


class ExclusionItem(QualityModel):
    checkpoint: str
    path: str
    reason: str


class JevDecisionItem(QualityModel):
    request_id: str
    policy: str
    subject: str
    stage: str
    verdict: str
    rule: str | None = None
    label: str | None = None


class QualityRunSummary(QualityModel):
    id: str
    experiment: str
    job: str
    trial: str
    arm: str
    problem: str
    attempt: int
    valid: bool
    checkpoints: int
    truncated_to: int | None = None
    score: Cell
    sources: dict[str, str | None]
    trial_record: str
    """``<jobs-root name>/<job dir>/trial-records/<trial>.json``: the join key to the run review."""


GateVerdict = Literal["passed", "refused", "timed_out", "error"]


class GateRound(QualityModel):
    """One commit-gate run of a step, read from the step's guardrail-gate record."""

    run: int
    after: str
    verdict: GateVerdict
    blocking: int | None = None
    listed: int = 0
    families: dict[str, int] | None = None
    """Blocking findings per rule family; None when the stored files do not name them all."""
    source: str
    summary: str
    log_path: str | None = None
    duration_s: float | None = None


class GateStep(QualityModel):
    """One checkpoint's gate effort: ``gate-effort`` cells and the gate's rounds."""

    checkpoint: str
    termination: str | None = None
    cells: dict[str, Cell]
    families: dict[str, Cell]
    first_run_families: dict[str, Cell]
    rounds: tuple[GateRound, ...] = ()


class GateView(QualityModel):
    """A gated trial's commit-gate effort (gate-effort); absent for an ungated trial."""

    source: str | None
    scorer: str | None = None
    """The gate-effort version the cells come from (None: the trial is unassessed)."""
    shadow: bool = False
    """A shadow gate (guardrail-gate@2 ``mode: shadow``): recorded at each step's end, never
    enforced; its fix outcomes are ``not_applicable`` and the families shown are first-run."""
    steps: tuple[GateStep, ...]
    totals: dict[str, Cell]
    notes: tuple[str, ...] = ()


class QualityRunDetail(QualityModel):
    run: QualityRunSummary
    policy: PolicyInfo
    rows: tuple[CheckpointRow, ...]
    problem: dict[str, Cell]
    policy_reasons: tuple[str, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    exclusions: tuple[ExclusionItem, ...] = ()
    jev_decisions: tuple[JevDecisionItem, ...] = ()
    notes: tuple[str, ...] = ()
    gate: GateView | None = None


class CurvePoint(QualityModel):
    """Mean across attempts with the analysis's 95% interval (``low``/``high``; two values)."""

    checkpoint: str
    n: int
    mean: float | None = None
    sd: float | None = None
    low: float | None = None
    high: float | None = None
    interval_method: str | None = None


class PairedDelta(QualityModel):
    component: str
    arm: str
    n: int
    mean: float | None = None
    sd: float | None = None
    ci: tuple[float, float] | None = None
    interval_method: str | None = None
    verdict: Verdict
    reason: str
    excluded: tuple[str, ...] = ()


class Distribution(QualityModel):
    arm: str
    n: int
    values: tuple[float, ...]
    quantiles: tuple[float, float, float, float, float] | None = None


class SignalDistributions(QualityModel):
    signal: str
    label: str
    unit: str
    source: str
    arms: tuple[Distribution, ...]


class ScatterPoint(QualityModel):
    checkpoint: str
    a: float
    b: float
    n_a: int
    n_b: int


class HeadToHeadSignal(QualityModel):
    signal: str
    label: str
    axis: str
    engine: str
    points: tuple[ScatterPoint, ...]
    excluded: tuple[str, ...] = ()


class HeadToHead(QualityModel):
    arm_a: str
    arm_b: str
    aggregation: Literal["median_across_attempts"] = "median_across_attempts"
    signals: tuple[HeadToHeadSignal, ...]


class Correlation(QualityModel):
    a: str
    b: str
    r: float | None = None
    reason: str | None = None


class Sensitivity(QualityModel):
    n: int
    weights: dict[str, float]
    ablation: dict[str, float]
    correlations: tuple[Correlation, ...]
    excluded: tuple[str, ...] = ()


class ProblemQuality(QualityModel):
    problem: str
    checkpoints: tuple[str, ...]
    policy: PolicyInfo
    baseline_arm: str | None
    arms: tuple[str, ...]
    run_ids: tuple[str, ...]
    curves: dict[str, dict[str, tuple[CurvePoint, ...]]]
    paired: tuple[PairedDelta, ...]
    distributions: tuple[SignalDistributions, ...]
    head_to_head: HeadToHead | None
    sensitivity: Sensitivity
    excluded: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class QualityExperiment(QualityModel):
    """One experiment under one lock: jobs under different locks are never pooled."""

    experiment: str
    lock_sha256: str | None
    jobs: tuple[str, ...]
    problems: tuple[ProblemQuality, ...]
    primary_outcome: str | None = None
    """The pre-registered primary outcome (the lock's ``analysis.primary_outcome``, else the
    progression's default), as the analysis report labels it."""
    primary_source: str | None = None
    primary_raw: str | None = None
    """The raw SCB counterpart an errata-applied primary is always reported beside."""


class QualityProjection(QualityModel):
    kind: Literal["raidar-quality-projection"] = "raidar-quality-projection"
    schema_version: Literal[1] = 1
    generated_from: str
    synthetic: bool = False
    runs: tuple[QualityRunSummary, ...]
    run_details: dict[str, QualityRunDetail]
    experiments: tuple[QualityExperiment, ...]

    @model_validator(mode="after")
    def _index_matches_details(self) -> Self:
        ids = [run.id for run in self.runs]
        if len(set(ids)) != len(ids):
            raise ValueError("runs contains duplicate ids")
        if set(ids) != set(self.run_details):
            raise ValueError("run index and run_details differ")
        return self
