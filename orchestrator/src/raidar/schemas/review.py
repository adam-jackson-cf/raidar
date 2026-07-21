"""Versioned contracts for persisted review-surface projections."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from raidar.schemas.scenario import (
    CatalogStatus,
    DeliveryMode,
    EvaluationRole,
    StartingCondition,
    TaskKind,
    WorkspaceState,
)

ReviewStatus = Literal["OK", "ERROR", "UNSET"]
ReviewSpanType = Literal["TRACE", "AGENT_ROOT", "LLM_GENERATION", "TOOL_CALL", "INTERNAL"]
RevisionChangeCategory = Literal[
    "initial-revision",
    "metadata",
    "environment",
    "starter",
    "verification",
    "requirements",
    "evidence",
    "visual",
    "scorers",
    "prompt",
    "rules",
]


class ResolvedScorerReviewConfig(BaseModel):
    """Exact resolved scorer configuration used by a scenario revision."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    category: str
    weight: float
    metrics: list[dict[str, Any]]


class ScenarioComparableConfig(BaseModel):
    """Semantic scenario configuration used for revision comparability."""

    model_config = ConfigDict(extra="forbid")

    scenario: str
    display_name: str
    revision: str
    parent_revision: str | None
    description: str
    difficulty: str
    task_kind: TaskKind
    delivery_mode: DeliveryMode
    workspace_state: WorkspaceState
    starting_conditions: list[StartingCondition]
    languages: list[str]
    evaluation_role: EvaluationRole
    catalog_status: CatalogStatus
    family_id: str | None
    variant_axes: dict[str, str]
    timeout_sec: int
    environment: dict[str, Any]
    starter: dict[str, Any]
    verification: dict[str, Any]
    requirements: dict[str, Any]
    evidence: dict[str, Any]
    visual: dict[str, Any] | None
    scorers: list[ResolvedScorerReviewConfig]
    prompt: dict[str, Any]
    prompt_artifacts: dict[str, str] = Field(default_factory=dict)
    rules_artifacts: dict[str, str] = Field(default_factory=dict)
    fingerprint: str


class DiffLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["context", "added", "removed"]
    text: str


class TextDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: int
    removed: int
    truncated: bool
    lines: list[DiffLine]


class FileDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    diff: TextDiff


class RevisionDiffArtifact(BaseModel):
    """Typed semantic diff between two scenario revisions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    key: str
    scenario: str
    from_revision: str
    to_revision: str
    change_categories: list[RevisionChangeCategory]
    summary: list[str]
    comparable_warnings: list[str]
    sections: dict[str, FileDiff]
    files: dict[str, FileDiff]


class ScenarioReviewContract(BaseModel):
    """Scenario semantics persisted with every experiment."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    comparable_config: ScenarioComparableConfig
    change_categories: list[RevisionChangeCategory]
    revision_diff: RevisionDiffArtifact | None = None


class ReviewSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    parent_span_id: str | None
    name: str
    span_type: ReviewSpanType
    status: ReviewStatus
    start_time_ms: int | None
    end_time_ms: int | None
    duration_ms: int | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    input_payload: str | None
    output_payload: str | None
    attributes: str | None


class ReviewAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    span_id: str | None
    kind: Literal["issue", "good", "note"]
    note: str | None
    source: Literal["raidar"] = "raidar"
    created_at: int
    category: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class ReviewRunRecord(BaseModel):
    """Stable run index representation consumed by the review application."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    scenario: str
    revision: str
    harness: str
    model: str
    reasoning_effort: str | None
    agent_spec: str
    experiment_id: str
    started_at: int
    duration_ms: int
    status: ReviewStatus
    span_count: int
    total_input_tokens: int
    total_output_tokens: int
    composite_score: float | None
    quality_score: float | None
    diagnostic_score: float | None
    unscored: bool
    unscored_reasons: list[str]
    valid: bool
    synthetic: bool
    metric_scores: list[dict[str, Any]]
    finding_counts: dict[str, int]
    issue_categories: dict[str, int]
    failed_gates: list[str]
    artifact_paths: dict[str, str | None]


class ReviewScenarioMetadata(BaseModel):
    """Filterable scenario taxonomy shown in experiment review records."""

    model_config = ConfigDict(extra="forbid")

    display_name: str
    description: str
    difficulty: str
    task_kind: TaskKind
    delivery_mode: DeliveryMode
    workspace_state: WorkspaceState
    starting_conditions: list[StartingCondition]
    languages: list[str]
    evaluation_role: EvaluationRole
    catalog_status: CatalogStatus
    family_id: str | None
    variant_axes: dict[str, str]
    timeout_sec: int


class ReviewRunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: ReviewRunRecord
    spans: list[ReviewSpan]
    annotations: list[ReviewAnnotation]


class ReviewExperimentRecord(BaseModel):
    """Experiment comparison record with its exact semantic configuration."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    dir: str
    scenario: str
    revision: str
    harness: str
    model: str
    reasoning_effort: str | None
    agent_spec: str
    synthetic: bool
    repeats: int
    scenario_meta: ReviewScenarioMetadata
    comparable_config: ScenarioComparableConfig
    change_categories: list[RevisionChangeCategory]
    aggregate: dict[str, Any]
    sample: dict[str, Any]
    rerun: dict[str, Any]
    findings: list[dict[str, Any]]
    created_at_utc: str | None
    run_ids: list[str]


class ExperimentReviewProjection(BaseModel):
    """One experiment's fully semantic, regenerable review projection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    experiment: ReviewExperimentRecord
    runs: list[ReviewRunDetail]
    revision_diff: RevisionDiffArtifact | None = None


class ReviewProjectionArtifact(BaseModel):
    """Merged projection copied by the JavaScript data build."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    generated_from: str
    experiments: list[ReviewExperimentRecord]
    runs: list[ReviewRunRecord]
    run_details: dict[str, ReviewRunDetail]
    revision_diffs: list[RevisionDiffArtifact]
