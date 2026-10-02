"""Versioned contract of the review-surface projection (``schema_version=2``).

The surface groups experiment records by ``problem`` (the problem id) and
``arm`` (the label of the arm's condition, so arms of one condition on
different harnesses share it) and ranks agent specs inside each group.
``arm_diffs`` are condition diffs between the baseline arm and another arm.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReviewStatus = Literal["OK", "ERROR", "UNSET"]
ReviewSpanType = Literal["TRACE", "AGENT_ROOT", "LLM_GENERATION", "TOOL_CALL", "INTERNAL"]
ArmChangeCategory = Literal[
    "baseline",
    "metadata",
    "environment",
    "starter",
    "prompt",
    "rules",
]


class ResolvedScorerReviewConfig(BaseModel):
    """Exact resolved scorer configuration an arm is assessed with."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    category: str
    weight: float
    metrics: list[dict[str, Any]]


class ArmComparableConfig(BaseModel):
    """Semantic configuration of one arm on one problem, used for arm comparability."""

    model_config = ConfigDict(extra="forbid")

    problem: str
    display_name: str
    arm: str
    parent_arm: str | None
    description: str
    difficulty: str
    languages: list[str]
    checkpoints: int
    timeout_sec: int
    environment: dict[str, Any]
    verification: dict[str, Any]
    scorers: list[ResolvedScorerReviewConfig]
    condition: dict[str, Any]
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


class ArmDiffArtifact(BaseModel):
    """Typed condition diff between the baseline arm and another arm of one problem."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    key: str
    problem: str
    from_arm: str
    to_arm: str
    change_categories: list[ArmChangeCategory]
    summary: list[str]
    comparable_warnings: list[str]
    sections: dict[str, FileDiff]
    files: dict[str, FileDiff]


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
    problem: str
    arm: str
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


class ReviewProblemMetadata(BaseModel):
    """Problem metadata shown in experiment review records."""

    model_config = ConfigDict(extra="forbid")

    display_name: str
    description: str
    difficulty: str
    languages: list[str]
    checkpoints: int
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
    problem: str
    arm: str
    harness: str
    model: str
    reasoning_effort: str | None
    agent_spec: str
    synthetic: bool
    repeats: int
    problem_meta: ReviewProblemMetadata
    comparable_config: ArmComparableConfig
    change_categories: list[ArmChangeCategory]
    aggregate: dict[str, Any]
    sample: dict[str, Any]
    rerun: dict[str, Any]
    findings: list[dict[str, Any]]
    created_at_utc: str | None
    run_ids: list[str]


class ReviewProjectionArtifact(BaseModel):
    """Merged projection copied by the JavaScript data build."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    generated_from: str
    experiments: list[ReviewExperimentRecord]
    runs: list[ReviewRunRecord]
    run_details: dict[str, ReviewRunDetail]
    arm_diffs: list[ArmDiffArtifact]
