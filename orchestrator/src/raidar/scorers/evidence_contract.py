"""Strict serialized evidence output contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator


class CommandEvidenceResult(BaseModel):
    """Bounded command result emitted by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    command: list[str]
    cwd: str
    duration_sec: float = Field(ge=0)
    exit_code: int
    stderr: str
    stdout: str
    timed_out: bool


class FileEvidenceResult(BaseModel):
    """Bounded file-read result emitted by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bytes: int = Field(ge=0)
    content: str
    path: str
    sha256: str
    truncated: bool


class JsonEvidenceResult(BaseModel):
    """Bounded JSON-read result emitted by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bytes: int = Field(ge=0)
    content: Any
    path: str
    sha256: str
    truncated: bool


class StructuralSnapshotFileEvidence(BaseModel):
    """Deterministic structural measures for one selected source file."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    bytes: int = Field(ge=0)
    code_line_count: int = Field(ge=0)
    line_count: int = Field(ge=0)
    nonempty_line_count: int = Field(ge=0)
    path: str
    sha256: str


class StructuralSnapshotEvidenceResult(BaseModel):
    """Deterministic structural snapshot for selected source files."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: str
    files: list[StructuralSnapshotFileEvidence] = Field(min_length=1)


class PublicContractFileEvidence(BaseModel):
    """Normalized public declarations for one selected source file."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    declarations: list[str]
    path: str
    sha256: str


class PublicContractSnapshotEvidenceResult(BaseModel):
    """Deterministic public-contract snapshot for selected source files."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: str
    files: list[PublicContractFileEvidence] = Field(min_length=1)


class GlobEvidenceResult(BaseModel):
    """Bounded workspace glob result emitted by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    matches: list[str]
    pattern: str


class VisualPathEvidence(BaseModel):
    """Presence record for one visual path."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    exists: bool
    path: str


class VisualComparisonEvidence(BaseModel):
    """One resolved visual-tool comparison result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    command: list[str]
    exit_code: int
    similarity: float = Field(ge=0, le=1)
    raw_output: str
    diff_path: str | None


class VisualRegionEvidence(BaseModel):
    """One weighted visual-region comparison result."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    weight: float = Field(ge=0)
    normalized_weight: float = Field(ge=0, le=1)
    actual: VisualPathEvidence
    reference: VisualPathEvidence
    diff: VisualPathEvidence
    comparison: VisualComparisonEvidence | None


class VisualDiffEvidenceResult(BaseModel):
    """Visual action result produced by a resolved visual tool invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    actual: VisualPathEvidence
    reference: VisualPathEvidence
    diff: VisualPathEvidence
    threshold: float = Field(ge=0, le=1)
    capture_succeeded: bool
    global_comparison: VisualComparisonEvidence | None = Field(alias="global")
    regions: list[VisualRegionEvidence]
    expected_region_count: int = Field(ge=0)
    available_region_count: int = Field(ge=0)


class ArchiveEvidenceResult(BaseModel):
    """Archive artifact result emitted by the runner."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    archive: str
    archived: list[str]
    exists: bool


class EvidenceActionError(BaseModel):
    """Sanitized action failure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    message: str
    type: str


EvidenceResult = (
    ArchiveEvidenceResult
    | CommandEvidenceResult
    | FileEvidenceResult
    | GlobEvidenceResult
    | JsonEvidenceResult
    | PublicContractSnapshotEvidenceResult
    | StructuralSnapshotEvidenceResult
    | VisualDiffEvidenceResult
)


class EvidenceActionResult(BaseModel):
    """Strict result for one normalized action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    kind: Literal[
        "archive",
        "command",
        "glob",
        "public_contract_snapshot",
        "read_file",
        "read_json",
        "structural_snapshot",
        "visual_diff",
    ]
    required: bool
    status: Literal["ok", "error", "skipped"]
    duration_sec: float = Field(ge=0)
    result: SkipValidation[EvidenceResult] | None = None
    error: EvidenceActionError | None = None

    @model_validator(mode="after")
    def _validate_status_payload(self) -> EvidenceActionResult:
        self._validate_status_fields()
        self._validate_typed_result()
        return self

    def _validate_status_fields(self) -> None:
        if self.status == "ok" and self.result is None:
            raise ValueError("successful evidence action requires result")
        if self.status == "ok" and self.error is not None:
            raise ValueError("successful evidence action cannot include an error")
        if self.status != "ok" and self.error is None:
            raise ValueError("unsuccessful evidence action requires error")
        if self.status != "ok" and self.result is not None:
            raise ValueError("unsuccessful evidence action cannot include a result")

    def _validate_typed_result(self) -> None:
        expected = {
            "archive": ArchiveEvidenceResult,
            "command": CommandEvidenceResult,
            "glob": GlobEvidenceResult,
            "read_file": FileEvidenceResult,
            "read_json": JsonEvidenceResult,
            "public_contract_snapshot": PublicContractSnapshotEvidenceResult,
            "structural_snapshot": StructuralSnapshotEvidenceResult,
            "visual_diff": VisualDiffEvidenceResult,
        }[self.kind]
        if self.result is not None:
            try:
                validated_result = expected.model_validate(self.result)
            except ValueError as exc:
                raise ValueError(
                    f"evidence action {self.id} has invalid {self.kind} result"
                ) from exc
            object.__setattr__(self, "result", validated_result)


class EvidenceArtifactResult(BaseModel):
    """One persisted artifact produced by an archive action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_id: str
    path: str
    exists: bool


class EvidenceRunnerMetadata(BaseModel):
    """Strict runner summary metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    required_error_count: int = Field(ge=0)
    workspace: str


class EvidenceOutput(BaseModel):
    """Complete current-version evidence output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"]
    runner_contract: Literal["python-evidence-runner@2"]
    aliases: dict[str, str]
    actions: list[EvidenceActionResult]
    artifacts: list[EvidenceArtifactResult]
    metadata: EvidenceRunnerMetadata

    @model_validator(mode="after")
    def _validate_identity_graph(self) -> EvidenceOutput:
        self._validate_action_identity()
        self._validate_metadata_counts()
        self._validate_artifact_identity()
        return self

    def _validate_action_identity(self) -> None:
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("evidence output action ids must be unique")
        unknown_aliases = sorted(set(self.aliases.values()) - set(action_ids))
        if unknown_aliases:
            raise ValueError(
                "evidence output aliases reference unknown actions: " + ", ".join(unknown_aliases)
            )

    def _validate_metadata_counts(self) -> None:
        if self.metadata.action_count != len(self.actions):
            raise ValueError("evidence output metadata.action_count does not match actions")
        error_count = sum(1 for action in self.actions if action.status != "ok")
        required_error_count = sum(
            1 for action in self.actions if action.required and action.status != "ok"
        )
        if self.metadata.error_count != error_count:
            raise ValueError("evidence output metadata.error_count does not match actions")
        if self.metadata.required_error_count != required_error_count:
            raise ValueError("evidence output metadata.required_error_count does not match actions")

    def _validate_artifact_identity(self) -> None:
        archive_ids = {action.id for action in self.actions if action.kind == "archive"}
        artifact_action_ids = [artifact.action_id for artifact in self.artifacts]
        unknown_artifacts = sorted(set(artifact_action_ids) - archive_ids)
        if unknown_artifacts:
            raise ValueError(
                "evidence output artifacts reference non-archive actions: "
                + ", ".join(unknown_artifacts)
            )
        if len(artifact_action_ids) != len(set(artifact_action_ids)):
            raise ValueError("evidence output artifact action ids must be unique")
