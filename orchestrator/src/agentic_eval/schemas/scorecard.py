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

    @computed_field
    @property
    def score(self) -> float:
        """Return visual similarity as score (0-1)."""
        return self.similarity


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
        return max(0.0, min(1.0, raw_score))


class ScaffoldAudit(BaseModel):
    """Scaffold baseline audit results."""

    manifest_version: str = "1.0.0"
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
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Scores
    functional: FunctionalScore = Field(default_factory=FunctionalScore)
    compliance: ComplianceScore = Field(default_factory=ComplianceScore)
    visual: VisualScore | None = Field(default_factory=VisualScore)
    efficiency: EfficiencyScore = Field(default_factory=EfficiencyScore)

    # Scaffold audit
    scaffold_audit: ScaffoldAudit | None = None

    @computed_field
    @property
    def composite_score(self) -> float:
        """Calculate weighted composite score.

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
