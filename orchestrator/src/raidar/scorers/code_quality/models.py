"""Typed V/E/H evidence, eligibility and problem-score models, ported from the SCB fork.

Source: ``slop-code-bench`` ``src/slop_code/metrics/scoring/models.py`` at
``25069ba``. Field names, types, validation rules and JSON serialisation
(Decimals as fixed-point strings) are unchanged, so a payload dumped here is
byte-comparable with the fork's (the G2 golden tests rely on this). Symbol,
lineage and regression models live in ``lineage_models``.

The fork's publication, generation, cost, benchmark and oracle-bundle models
are not ported: the Assessment store replaces that lifecycle (plan WS-G G2).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

DECIMAL_PLACES = Decimal("0.000000000001")
SCORE_PLACES = Decimal("0.000001")
ZERO = Decimal("0")
ONE = Decimal("1")
ELIGIBILITY_CODES = frozenset(
    {
        "canonical_artifact_invalid",
        "canonical_provenance_unavailable",
        "canonical_test_denominator_zero",
        "configured_problem_too_short",
        "coverage_parity_failed",
        "coverage_parity_unavailable",
        "measurement_environment_mismatch",
        "production_language_unsupported",
        "production_source_loc_zero",
        "production_symbol_evidence_invalid",
        "score_evidence_invalid",
        "score_formula_invalid",
    }
)
_SHA256 = r"^[0-9a-f]{64}$"


def canonical_decimal(value: Decimal, places: Decimal = DECIMAL_PLACES) -> Decimal:
    """Return a finite decimal quantized at the artifact boundary."""
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return value.quantize(places)


class ScoringModel(BaseModel):
    """Frozen base for every persisted canonical scoring artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, ser_json_inf_nan="strings")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_decimals(self, value: object) -> object:
        return format(value, "f") if isinstance(value, Decimal) else value


UnitDecimal = Annotated[Decimal, Field(ge=ZERO, le=ONE)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=ZERO)]


class FileRole(StrEnum):
    GENERATED = "generated"
    TEST = "test"
    PRODUCTION = "production"
    IGNORED = "ignored"


class QualityInterpreterEvidence(ScoringModel):
    executable: str = Field(min_length=1)
    implementation: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cache_tag: str = Field(min_length=1)
    executable_sha256: str = Field(pattern=_SHA256)
    ast_sha256: str = Field(pattern=_SHA256)
    tokenize_sha256: str = Field(pattern=_SHA256)


class QualityFileEvidence(ScoringModel):
    path: str = Field(min_length=1)
    hash: str = Field(pattern=_SHA256)
    source_lines: tuple[int, ...]


class QualityRuffDiagnosticEvidence(ScoringModel):
    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    start: tuple[int, int]
    end: tuple[int, int]


class QualityRuffEvidence(ScoringModel):
    version: str = Field(min_length=1)
    invocation: tuple[str, ...]
    rules: tuple[str, ...]
    diagnostics: tuple[QualityRuffDiagnosticEvidence, ...]


class QualityCloneGroupEvidence(ScoringModel):
    vector: tuple[str, ...] = Field(min_length=1)
    occurrences: tuple[tuple[str, int, int], ...] = Field(min_length=2)


class EligibleSymbolEvidence(ScoringModel):
    identity: tuple[str, str, Literal["function", "method"], int, int]
    cc: NonNegativeDecimal
    sloc: int = Field(gt=0)
    mass: NonNegativeDecimal
    high_complexity: bool


class SymbolExclusionReason(StrEnum):
    KIND_INELIGIBLE = "symbol_kind_ineligible"
    PATH_NOT_PRODUCTION = "symbol_path_not_production"


class ExcludedSymbolEvidence(ScoringModel):
    reason: SymbolExclusionReason
    kind: str
    path: str


class QualityDecimalContextEvidence(ScoringModel):
    precision: int = Field(gt=0)
    rounding: Literal["ROUND_HALF_EVEN"]


def _is_sorted(values: tuple[Any, ...], key: Callable[[Any], Any] | None = None) -> bool:
    return tuple(sorted(values, key=key)) == values


class ProductionQualityRawEvidence(ScoringModel):
    """Complete, ordered raw inputs required to recompute V and E."""

    checkpoint_id: str = Field(min_length=1)
    interpreter: QualityInterpreterEvidence
    parser_tokenizer_schema_id: str = Field(pattern=_SHA256)
    parser_tokenizer_probe: str = Field(min_length=1)
    files: tuple[QualityFileEvidence, ...]
    ruff: QualityRuffEvidence
    clones: tuple[QualityCloneGroupEvidence, ...]
    verbosity_lines: tuple[tuple[str, int], ...]
    clone_lines: tuple[tuple[str, int], ...]
    union_numerator: int = Field(ge=0)
    source_loc_denominator: int = Field(gt=0)
    eligible_symbols: tuple[EligibleSymbolEvidence, ...]
    excluded_symbols: tuple[ExcludedSymbolEvidence, ...]
    erosion_numerator: NonNegativeDecimal
    erosion_denominator: NonNegativeDecimal
    decimal_context: QualityDecimalContextEvidence
    verbosity: UnitDecimal
    erosion: UnitDecimal

    @model_validator(mode="after")
    def validate_quality_counts(self) -> ProductionQualityRawEvidence:
        if self.union_numerator > self.source_loc_denominator:
            raise ValueError("verbosity numerator cannot exceed source LOC")
        if self.erosion_numerator > self.erosion_denominator:
            raise ValueError("erosion numerator cannot exceed denominator")
        if not _is_sorted(self.files, key=lambda row: row.path.encode("utf-8")):
            raise ValueError("quality files must be ordered")
        if not (_is_sorted(self.verbosity_lines) and _is_sorted(self.clone_lines)):
            raise ValueError("quality line evidence must be ordered")
        self._validate_orders()
        return self

    def _validate_orders(self) -> None:
        if not _is_sorted(
            self.ruff.diagnostics, key=lambda row: (row.path, row.start, row.end, row.code)
        ):
            raise ValueError("Ruff diagnostics must be ordered")
        if not _is_sorted(self.clones, key=lambda row: (row.vector, row.occurrences)):
            raise ValueError("clone groups must be ordered")
        if not _is_sorted(self.eligible_symbols, key=lambda row: row.identity):
            raise ValueError("eligible symbols must be ordered")
        if not _is_sorted(
            self.excluded_symbols,
            key=lambda row: (row.reason.value, row.path.encode("utf-8"), row.kind.encode("utf-8")),
        ):
            raise ValueError("excluded symbols must be ordered")


class GraphNode(ScoringModel):
    identity: str = Field(min_length=1)
    path: str = Field(min_length=1)


class GraphEdge(ScoringModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    weight: NonNegativeDecimal
    source_role: FileRole = FileRole.PRODUCTION
    target_role: FileRole = FileRole.PRODUCTION
    cyclic: bool = False
    self_loop: bool = False


class GraphSccEvidence(ScoringModel):
    members: tuple[str, ...] = Field(min_length=1)
    mass: NonNegativeDecimal
    cyclic: bool = False
    self_loop: bool = False

    @model_validator(mode="after")
    def validate_members(self) -> GraphSccEvidence:
        if tuple(sorted(set(self.members), key=lambda value: value.encode("utf-8"))) != (
            self.members
        ):
            raise ValueError("SCC members must be unique UTF-8 byte order")
        return self


class ProductionGraphEvidence(ScoringModel):
    checkpoint_id: str = Field(min_length=1)
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    sccs: tuple[GraphSccEvidence, ...]
    cyclic_mass: NonNegativeDecimal
    total_mass: NonNegativeDecimal
    architecture: UnitDecimal
    cross_role_edges: tuple[GraphEdge, ...] = ()

    @model_validator(mode="after")
    def validate_masses(self) -> ProductionGraphEvidence:
        if self.cyclic_mass > self.total_mass:
            raise ValueError("cyclic mass cannot exceed total mass")
        if sum((edge.weight for edge in self.edges), ZERO) != self.total_mass:
            raise ValueError("total mass must equal production edge weights")
        cyclic_scc_mass = sum((scc.mass for scc in self.sccs if scc.cyclic), ZERO)
        if cyclic_scc_mass != self.cyclic_mass:
            raise ValueError("cyclic mass must equal cyclic SCC mass")
        return self


class Eligibility(ScoringModel):
    eligible: bool
    reasons: tuple[str, ...] = ()

    @field_validator("reasons")
    @classmethod
    def validate_reasons(cls, reasons: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(reasons))) != reasons:
            raise ValueError("eligibility reasons must be deduplicated ASCII-sorted")
        if unknown := set(reasons) - ELIGIBILITY_CODES:
            raise ValueError(f"unknown eligibility codes: {sorted(unknown)}")
        return reasons

    @model_validator(mode="after")
    def validate_state(self) -> Eligibility:
        if self.eligible != (not self.reasons):
            raise ValueError("eligible requires no reasons and ineligible requires reasons")
        return self


class ProductionEvidence(ScoringModel):
    """Typed V/E/H payloads and their complete eligibility decision."""

    eligibility: Eligibility
    production_quality: ProductionQualityRawEvidence | None = None
    production_graph: ProductionGraphEvidence | None = None

    @model_validator(mode="after")
    def validate_complete_eligible_evidence(self) -> ProductionEvidence:
        if self.eligibility.eligible and (
            self.production_quality is None or self.production_graph is None
        ):
            raise ValueError("eligible production evidence requires V/E/H payloads")
        return self


class CheckpointCorrectnessEvidence(ScoringModel):
    checkpoint_id: str = Field(min_length=1)
    produced: bool = True
    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: UnitDecimal

    @model_validator(mode="after")
    def validate_rate(self) -> CheckpointCorrectnessEvidence:
        if not self.produced:
            if self.passed or self.total or self.rate != ZERO:
                raise ValueError("unproduced correctness must be the declared zero")
        elif self.total <= 0:
            raise ValueError("produced correctness requires a positive denominator")
        elif self.rate != Decimal(self.passed) / Decimal(self.total):
            raise ValueError("correctness rate must equal passed / total")
        return self


class ComponentScores(ScoringModel):
    correctness: UnitDecimal
    verbosity: UnitDecimal
    erosion: UnitDecimal
    architecture: UnitDecimal
    rework: UnitDecimal
    regression: UnitDecimal
    inertia: UnitDecimal


class ProblemScore(ScoringModel):
    problem_id: str = Field(min_length=1)
    configured_checkpoints: int = Field(ge=2)
    checkpoint_correctness: tuple[CheckpointCorrectnessEvidence, ...]
    components: ComponentScores
    score: Annotated[Decimal, Field(ge=ZERO, le=Decimal("100"))]

    @model_validator(mode="after")
    def validate_checkpoints(self) -> ProblemScore:
        if len(self.checkpoint_correctness) != self.configured_checkpoints:
            raise ValueError("correctness evidence must cover configured checkpoints")
        identifiers = {item.checkpoint_id for item in self.checkpoint_correctness}
        if len(identifiers) != len(self.checkpoint_correctness):
            raise ValueError("correctness checkpoint identifiers must be unique")
        return self
