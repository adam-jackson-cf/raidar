"""Symbol, lineage (R) and regression-attribution (G) models, ported from the SCB fork.

Source: ``slop-code-bench`` ``src/slop_code/metrics/scoring/models.py`` at
``25069ba``. Names, fields, validation and JSON serialisation are unchanged so
that payloads compare byte-for-byte with the fork's in the G2 golden tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from raidar.scoring.code_quality.models import (
    Eligibility,
    ScoringModel,
    UnitDecimal,
)

_SHA256 = r"^[0-9a-f]{64}$"
_HEX = frozenset("0123456789abcdef")
StableIdentity = tuple[str, str, str, str, str, int, int]


class SymbolIdentity(ScoringModel):
    """Canonical identity and immutable content fingerprints for one symbol."""

    path: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    kind: Literal["function", "method"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    language: str = "python"
    parent_qualified_name: str = ""
    body_sha256: str = ""
    structure_sha256: str = ""
    signature_sha256: str = ""

    @model_validator(mode="after")
    def validate_span(self) -> SymbolIdentity:
        if self.end_line < self.start_line:
            raise ValueError("end line precedes start line")
        for value in (self.body_sha256, self.structure_sha256, self.signature_sha256):
            if value and (len(value) != 64 or any(char not in _HEX for char in value)):
                raise ValueError("symbol hashes must be lowercase SHA-256 digests")
        return self

    @property
    def exact_identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.language,
            self.kind,
            self.parent_qualified_name,
            self.qualified_name,
            self.path,
        )

    @property
    def stable_identity(self) -> StableIdentity:
        return (*self.exact_identity, self.start_line, self.end_line)


class LineageCandidate(ScoringModel):
    """One complete-bipartite candidate edge between checkpoint symbols."""

    prior: SymbolIdentity
    current: SymbolIdentity
    body_hash_equal: bool
    structure_hash_equal: bool
    signature_hash_equal: bool
    accepted: bool
    ambiguous_set_id: str | None
    match: Literal["exact", "hash", "none"] = "exact"

    @model_validator(mode="after")
    def validate_candidate(self) -> LineageCandidate:
        if self.accepted and self.ambiguous_set_id is not None:
            raise ValueError("accepted lineage cannot have an ambiguous set")
        if self.accepted and self.match == "none":
            raise ValueError("accepted lineage requires a match kind")
        if self.match == "exact" and self.prior.exact_identity != self.current.exact_identity:
            raise ValueError("exact lineage must have equal exact identities")
        if self.match == "hash" and not (self.body_hash_equal and self.structure_hash_equal):
            raise ValueError("hash lineage requires body and structure equality")
        return self


class SymbolDegreeEvidence(ScoringModel):
    symbol: SymbolIdentity
    inbound: int = Field(ge=0)
    outbound: int = Field(ge=0)


class LineageComponentEvidence(ScoringModel):
    component_id: str = Field(min_length=64, max_length=64)
    priors: tuple[SymbolIdentity, ...]
    currents: tuple[SymbolIdentity, ...]
    ambiguous: bool

    @model_validator(mode="after")
    def validate_component(self) -> LineageComponentEvidence:
        if not self.priors or not self.currents:
            raise ValueError("lineage component requires both endpoint sets")
        return self


class OwnedLineEvidence(ScoringModel):
    symbol: SymbolIdentity
    lines: tuple[int, ...]

    @model_validator(mode="after")
    def validate_lines(self) -> OwnedLineEvidence:
        if tuple(sorted(set(self.lines))) != self.lines:
            raise ValueError("owned lines must be unique and ordered")
        if any(line < self.symbol.start_line or line > self.symbol.end_line for line in self.lines):
            raise ValueError("owned lines must be within the symbol span")
        return self


class PhysicalLineEvidence(ScoringModel):
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    side: Literal["added", "removed"]
    owner: SymbolIdentity | None = None


class LineageLedgerEntry(ScoringModel):
    candidate: LineageCandidate
    disposition: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1)
    prior_modified_before: bool = False
    current_modified_before: bool = False

    @model_validator(mode="after")
    def validate_disposition(self) -> LineageLedgerEntry:
        if self.candidate.accepted != (self.disposition == "accepted"):
            raise ValueError("lineage ledger disposition must match candidate")
        return self


class LedgerTransitionEvidence(ScoringModel):
    prior: SymbolIdentity
    current: SymbolIdentity
    input_modified_before: bool
    mapped_changed: bool
    output_modified_before: bool
    reset: bool = False


class TransitionReworkEvidence(ScoringModel):
    changed_physical_lines: tuple[PhysicalLineEvidence, ...]
    lineage_candidates: tuple[LineageCandidate, ...]
    symbol_degrees: tuple[SymbolDegreeEvidence, ...]
    lineage_components: tuple[LineageComponentEvidence, ...]
    owned_lines: tuple[OwnedLineEvidence, ...]
    lineage_ledger: tuple[LineageLedgerEntry, ...]
    ledger_transitions: tuple[LedgerTransitionEvidence, ...]
    changed_lines: int = Field(ge=0)
    repeated_lines: int = Field(ge=0)
    rework: UnitDecimal

    @model_validator(mode="after")
    def validate_counts(self) -> TransitionReworkEvidence:
        if self.repeated_lines > self.changed_lines:
            raise ValueError("repeated lines cannot exceed changed lines")
        if tuple(entry.candidate for entry in self.lineage_ledger) != self.lineage_candidates:
            raise ValueError("lineage ledger must be ordered by lineage candidates")
        return self


class ReworkCheckpointInput(ScoringModel):
    """One configured checkpoint; ``symbols=None`` is an explicit gap."""

    checkpoint_id: str = Field(min_length=1)
    symbols: tuple[SymbolIdentity, ...] | None
    removed_lines: tuple[PhysicalLineEvidence, ...] = ()
    added_lines: tuple[PhysicalLineEvidence, ...] = ()


class ProblemReworkEvidence(ScoringModel):
    """Ordered transition evidence and ledger state for one configured problem."""

    checkpoint_ids: tuple[str, ...] = Field(min_length=2)
    transitions: tuple[TransitionReworkEvidence, ...]
    final_ledger: tuple[LedgerTransitionEvidence, ...]

    @model_validator(mode="after")
    def validate_transitions(self) -> ProblemReworkEvidence:
        if len(self.transitions) != len(self.checkpoint_ids) - 1:
            raise ValueError("rework transitions must cover adjacent checkpoints")
        return self


class RegressionPhase(StrEnum):
    COLLECTION = "collection"
    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"


class RegressionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class CorpusEvidence(ScoringModel):
    corpus_id: str = Field(pattern=_SHA256)
    manifest_sha256: str = Field(pattern=_SHA256)


class EvaluatorEnvironmentEvidence(ScoringModel):
    environment_id: str = Field(pattern=_SHA256)
    project_sha256: str = Field(pattern=_SHA256)
    lock_sha256: str = Field(pattern=_SHA256)
    platform_sha256: str = Field(pattern=_SHA256)
    plugin_sha256: str = Field(pattern=_SHA256)
    interpreter_sha256: str = Field(pattern=_SHA256)


class RegressionInvocationEvidence(ScoringModel):
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = Field(min_length=1)


class RegressionOutcomeEvidence(ScoringModel):
    node_id: str = Field(min_length=1)
    group_type: str = Field(min_length=1)
    phase: RegressionPhase
    outcome: RegressionOutcome


class RegressionLedgerEvidence(ScoringModel):
    node_id: str = Field(min_length=1)
    phase: RegressionPhase
    outcome: RegressionOutcome


class CoverageContextEvidence(ScoringModel):
    node_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    executed_lines: tuple[int, ...]

    @model_validator(mode="after")
    def validate_lines(self) -> CoverageContextEvidence:
        if tuple(sorted(set(self.executed_lines))) != self.executed_lines:
            raise ValueError("coverage lines must be unique and ordered")
        if any(line < 1 for line in self.executed_lines):
            raise ValueError("coverage lines must be positive")
        return self


class ProcessAuditEvent(ScoringModel):
    node_id: str = Field(min_length=1)
    phase: RegressionPhase
    event: Literal[
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.fork",
        "pty.spawn",
    ]


class RegressionRunEvidence(ScoringModel):
    environment: EvaluatorEnvironmentEvidence
    corpus: CorpusEvidence
    invocation: RegressionInvocationEvidence
    outcomes: tuple[RegressionOutcomeEvidence, ...]
    ledger: tuple[RegressionLedgerEvidence, ...]
    coverage: tuple[CoverageContextEvidence, ...]
    process_events: tuple[ProcessAuditEvent, ...] = ()


class RegressionBreadthInput(ScoringModel):
    transition_id: str = Field(min_length=1)
    baseline: RegressionRunEvidence
    produced: RegressionRunEvidence
    changed_symbols: tuple[SymbolIdentity, ...]


class RegressionSymbolAttribution(ScoringModel):
    node_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    symbol: SymbolIdentity


class RegressionAttribution(ScoringModel):
    """Persisted input, exact attribution, and eligibility for transition G."""

    input: RegressionBreadthInput
    attributions: tuple[RegressionSymbolAttribution, ...]
    eligibility: Eligibility
    regression: UnitDecimal | None

    @model_validator(mode="after")
    def validate_regression(self) -> RegressionAttribution:
        if self.eligibility.eligible != (self.regression is not None):
            raise ValueError("eligible regression attribution requires a score")
        if not self.eligibility.eligible and self.attributions:
            raise ValueError("ineligible regression attribution cannot attribute")
        return self
