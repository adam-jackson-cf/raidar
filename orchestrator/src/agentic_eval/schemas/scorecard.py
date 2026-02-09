"""Scorecard schemas for evaluation results.

Multi-dimensional scoring: functional, compliance, visual, efficiency.
Uses @computed_field for auto-calculated scores.
"""

from typing import Any

from pydantic import BaseModel, Field, computed_field

from ..config import settings
from .events import GateEvent, SessionEvent


class FunctionalScore(BaseModel):
    """Functional test results with auto-computed score."""

    passed: bool = False
    tests_passed: int = 0
    tests_total: int = 0
    build_succeeded: bool = False
    gates_passed: int = 0
    gates_total: int = 0

    @computed_field
    @property
    def score(self) -> float:
        """Calculate functional score (0-1)."""
        if not self.build_succeeded:
            return 0.0
        if self.tests_total == 0:
            return 1.0 if self.passed else 0.0
        return self.tests_passed / self.tests_total


class ComplianceCheck(BaseModel):
    """Result of a single compliance check."""

    rule: str = Field(description="Rule description")
    type: str = Field(description="Check type (deterministic or llm_judge)")
    passed: bool = Field(description="Whether check passed")
    evidence: str | None = Field(default=None, description="Supporting evidence")


class ComplianceScore(BaseModel):
    """Compliance evaluation with auto-computed score."""

    checks: list[ComplianceCheck] = Field(default_factory=list)

    @computed_field
    @property
    def score(self) -> float:
        """Calculate compliance score (0-1)."""
        if not self.checks:
            return 1.0
        passed_count = sum(1 for check in self.checks if check.passed)
        return passed_count / len(self.checks)


class VisualScore(BaseModel):
    """Visual regression score."""

    similarity: float = 0.0
    diff_path: str | None = None
    capture_succeeded: bool = False
    capture_error: str | None = None
    threshold: float | None = None

    @computed_field
    @property
    def score(self) -> float:
        """Return visual similarity as score (0-1)."""
        return self.similarity

    @computed_field
    @property
    def threshold_met(self) -> bool | None:
        """Whether similarity meets configured threshold."""
        if self.threshold is None:
            return None
        return self.similarity >= self.threshold


class EfficiencyScore(BaseModel):
    """Efficiency score based on gate failures."""

    total_gate_failures: int = 0
    unique_failure_categories: int = 0
    repeat_failures: int = 0

    @computed_field
    @property
    def score(self) -> float:
        """Calculate efficiency score (0-1).

        Formula: max(0, 1 - (gate_failures / max_failures) - (repeat_failures * penalty))
        """
        max_failures = settings.efficiency.max_gate_failures
        repeat_penalty = settings.efficiency.repeat_penalty
        raw_score = (
            1.0
            - (self.total_gate_failures / max_failures)
            - (self.repeat_failures * repeat_penalty)
        )
        return round(max(0.0, min(1.0, raw_score)), 3)


class CoverageScore(BaseModel):
    """Measured test coverage against a required threshold."""

    threshold: float | None = None
    measured: float | None = None
    source: str | None = None
    passed: bool = True


class RequirementCoverageScore(BaseModel):
    """Requirement presence and requirement-to-test mapping coverage."""

    total_requirements: int = 0
    satisfied_requirements: int = 0
    mapped_requirements: int = 0
    missing_requirement_ids: list[str] = Field(default_factory=list)
    requirement_gap_ids: list[str] = Field(default_factory=list)
    requirement_pattern_gaps: dict[str, list[str]] = Field(default_factory=dict)

    @computed_field
    @property
    def presence_ratio(self) -> float:
        """Fraction of requirements satisfied by implementation."""
        if self.total_requirements == 0:
            return 1.0
        return self.satisfied_requirements / self.total_requirements

    @computed_field
    @property
    def mapping_ratio(self) -> float:
        """Fraction of requirements with test mapping evidence."""
        if self.total_requirements == 0:
            return 1.0
        return self.mapped_requirements / self.total_requirements


class QualificationCheck(BaseModel):
    """Single hard-gate qualification result."""

    name: str = Field(description="Qualification check name")
    passed: bool = Field(description="Whether the check passed")
    evidence: str | None = Field(default=None, description="Check evidence")


class QualificationScore(BaseModel):
    """Hard-gate qualification aggregate."""

    checks: list[QualificationCheck] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        """All qualification checks must pass."""
        if not self.checks:
            return True
        return all(check.passed for check in self.checks)


class OptimizationScore(BaseModel):
    """Optimization metrics used after qualification succeeds."""

    uncached_input_tokens: int = 0
    output_tokens: int = 0
    command_count: int = 0
    failed_command_count: int = 0
    verification_rounds: int = 0
    repeated_verification_failures: int = 0

    @computed_field
    @property
    def score(self) -> float:
        """Compute optimization score from deterministic process metrics."""
        cfg = settings.optimization
        token_penalty = min(1.0, self.uncached_input_tokens / cfg.max_uncached_tokens)
        command_penalty = min(1.0, self.command_count / cfg.max_commands)
        failure_penalty = min(1.0, self.failed_command_count / cfg.max_failed_commands)
        extra_rounds = max(0, self.verification_rounds - 1)
        round_penalty = min(1.0, extra_rounds / cfg.max_extra_verification_rounds)
        repeat_penalty = min(1.0, self.repeated_verification_failures / cfg.max_repeat_failures)

        weighted_penalty = (
            token_penalty * cfg.token_weight
            + command_penalty * cfg.command_weight
            + failure_penalty * cfg.failure_weight
            + round_penalty * cfg.verification_round_weight
            + repeat_penalty * cfg.repeat_failure_weight
        )
        return round(max(0.0, min(1.0, 1.0 - weighted_penalty)), 3)


class ScaffoldAudit(BaseModel):
    """Scaffold baseline audit results."""

    manifest_version: str = "1.0.0"
    template: str | None = None
    template_version: str | None = None
    manifest_fingerprint: str | None = None
    file_count: int = 0
    dependency_count: int = 0
    changes_from_baseline: list[str] = Field(default_factory=list)


class Scorecard(BaseModel):
    """Complete scorecard for an evaluation run."""

    run_id: str = ""
    task_name: str = ""
    agent: str = ""
    model: str = ""
    rules_variant: str = ""
    duration_sec: float = 0.0
    terminated_early: bool = False
    termination_reason: str | None = None
    voided: bool = False
    void_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Scores
    functional: FunctionalScore = Field(default_factory=FunctionalScore)
    compliance: ComplianceScore = Field(default_factory=ComplianceScore)
    visual: VisualScore | None = Field(default_factory=VisualScore)
    efficiency: EfficiencyScore = Field(default_factory=EfficiencyScore)
    coverage: CoverageScore = Field(default_factory=CoverageScore)
    requirements: RequirementCoverageScore = Field(default_factory=RequirementCoverageScore)
    qualification: QualificationScore = Field(default_factory=QualificationScore)
    optimization: OptimizationScore = Field(default_factory=OptimizationScore)

    # Scaffold audit
    scaffold_audit: ScaffoldAudit | None = None

    @computed_field
    @property
    def quality_score(self) -> float:
        """Calculate weighted quality score from quality dimensions.

        Weights from config. If visual is None, redistributes visual weight proportionally.
        """
        w = settings.weights
        visual_score = self.visual.score if self.visual else 0.0

        if self.visual:
            return (
                self.functional.score * w.functional
                + self.compliance.score * w.compliance
                + visual_score * w.visual
                + self.efficiency.score * w.efficiency
            )

        # Redistribute visual weight proportionally to other dimensions
        non_visual_total = w.functional + w.compliance + w.efficiency
        func_adj = w.functional / non_visual_total
        comp_adj = w.compliance / non_visual_total
        eff_adj = w.efficiency / non_visual_total

        return (
            self.functional.score * func_adj
            + self.compliance.score * comp_adj
            + self.efficiency.score * eff_adj
        )

    @computed_field
    @property
    def composite_score(self) -> float:
        """Compute gated final score.

        Unqualified runs always receive 0. Qualified runs are ranked on optimization score.
        """
        if self.voided:
            return 0.0
        if not self.qualification.passed:
            return 0.0
        return self.optimization.score

    @computed_field
    @property
    def diagnostic_score(self) -> float:
        """Compute non-gating diagnostic score for comparing failed runs.

        This score intentionally does not gate on qualification so disqualified runs
        can still be ranked for analysis.
        """
        return round(
            (self.quality_score * 0.6)
            + (self.requirements.mapping_ratio * 0.25)
            + (self.optimization.score * 0.15),
            3,
        )


class EvalConfig(BaseModel):
    """Configuration for an evaluation run."""

    model: str = Field(description="Model identifier (provider/name)")
    harness: str = Field(description="Harness/agent name")
    rules_variant: str = Field(description="Rules variant used")
    task_name: str = Field(description="Task identifier")
    scaffold_template: str = Field(description="Scaffold template name")
    scaffold_version: str = Field(description="Scaffold template version")


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
