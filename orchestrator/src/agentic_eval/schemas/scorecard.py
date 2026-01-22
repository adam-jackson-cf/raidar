"""Scorecard schemas for evaluation results."""

from pydantic import BaseModel, Field

from .events import GateEvent, SessionEvent


class FunctionalScore(BaseModel):
    """Functional test results."""

    passed: bool = Field(description="Whether all tests passed")
    tests_passed: int = Field(description="Number of tests passed")
    tests_total: int = Field(description="Total number of tests")
    build_succeeded: bool = Field(description="Whether build succeeded")


class ComplianceCheck(BaseModel):
    """Result of a single compliance check."""

    rule: str = Field(description="Rule description")
    type: str = Field(description="Check type (deterministic or llm_judge)")
    passed: bool = Field(description="Whether check passed")
    evidence: str | None = Field(default=None, description="Supporting evidence")


class ComplianceScore(BaseModel):
    """Compliance evaluation results."""

    score: float = Field(ge=0, le=1, description="Compliance score 0-1")
    checks: list[ComplianceCheck] = Field(default_factory=list)


class VisualScore(BaseModel):
    """Visual regression results."""

    similarity: float = Field(ge=0, le=1, description="Similarity score 0-1")
    diff_path: str | None = Field(default=None, description="Path to diff image")


class EfficiencyScore(BaseModel):
    """Efficiency metrics based on gate failures."""

    total_gate_failures: int = Field(description="Total gate failures")
    unique_failure_categories: int = Field(description="Unique failure types")
    repeat_failures: int = Field(description="Number of repeated failures")
    score: float = Field(ge=0, le=1, description="Efficiency score 0-1")


class ScaffoldAudit(BaseModel):
    """Scaffold baseline audit results."""

    manifest_version: str = Field(description="Manifest version")
    file_count: int = Field(description="Number of tracked files")
    dependency_count: int = Field(description="Number of dependencies")
    changes_from_baseline: list[str] = Field(
        default_factory=list,
        description="List of changes detected",
    )


class Scorecard(BaseModel):
    """Complete scorecard for an evaluation run."""

    functional: FunctionalScore = Field(description="Functional test results")
    compliance: ComplianceScore = Field(description="Compliance results")
    visual: VisualScore | None = Field(default=None, description="Visual results")
    efficiency: EfficiencyScore = Field(description="Efficiency results")
    composite: float = Field(ge=0, le=1, description="Weighted composite score")
    scaffold_audit: ScaffoldAudit | None = Field(
        default=None,
        description="Scaffold baseline audit",
    )


class EvalConfig(BaseModel):
    """Configuration for an evaluation run."""

    model: str = Field(description="Model identifier (provider/name)")
    harness: str = Field(description="Harness/agent name")
    rules_variant: str = Field(description="Rules variant used")
    task_name: str = Field(description="Task identifier")


class EvalRun(BaseModel):
    """Complete evaluation run record."""

    id: str = Field(description="Unique run identifier")
    timestamp: str = Field(description="ISO timestamp of run start")
    config: EvalConfig = Field(description="Run configuration")
    duration_sec: float = Field(description="Run duration in seconds")
    terminated_early: bool = Field(
        default=False,
        description="Whether run terminated early",
    )
    termination_reason: str | None = Field(
        default=None,
        description="Reason for early termination",
    )
    scores: Scorecard = Field(description="Evaluation scores")
    events: list[SessionEvent] = Field(
        default_factory=list,
        description="Session events",
    )
    gate_history: list[GateEvent] = Field(
        default_factory=list,
        description="Gate execution history",
    )
