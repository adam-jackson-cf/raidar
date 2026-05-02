"""Scorecard schemas for experiment results."""

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from ..config import settings
from .events import GateEvent, TraceEvent


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
        if not self.build_succeeded:
            return 0.0
        if self.tests_total == 0:
            return 1.0 if self.passed else 0.0
        return self.tests_passed / self.tests_total


class AcceptanceCheck(BaseModel):
    """Result of a single acceptance check."""

    rule: str = Field(description="Rule description")
    type: str = Field(description="Check type (deterministic or llm_judge)")
    passed: bool = Field(description="Whether check passed")
    evidence: str | None = Field(default=None, description="Supporting evidence")


class AcceptanceScore(BaseModel):
    """Acceptance evaluation with auto-computed score."""

    checks: list[AcceptanceCheck] = Field(default_factory=list)

    @computed_field
    @property
    def score(self) -> float:
        if not self.checks:
            return 1.0
        passed_count = sum(1 for check in self.checks if check.passed)
        return passed_count / len(self.checks)


class VisualScore(BaseModel):
    """Visual regression score."""

    similarity: float = 0.0
    global_similarity: float | None = None
    regional_similarity: float | None = None
    worst_region_similarity: float | None = None
    contract_version: str | None = None
    region_decent_pass_rate: float | None = None
    policy_score: float | None = None
    passed: bool | None = None
    fidelity_tier: Literal["failed", "passed", "high_fidelity"] | None = None
    expected_region_count: int = 0
    available_region_count: int = 0
    region_evidence_status: Literal["present", "partial", "missing", "not_configured"] = (
        "not_configured"
    )
    actual_path: str | None = None
    reference_path: str | None = None
    diff_path: str | None = None
    capture_succeeded: bool = False
    capture_error: str | None = None
    regional_scores: list[dict[str, Any]] = Field(default_factory=list)

    @computed_field
    @property
    def score(self) -> float:
        return self.similarity


class VerificationStabilityScore(BaseModel):
    """Verification stability score based on gate failures."""

    total_gate_failures: int = 0
    unique_failure_categories: int = 0
    repeat_failures: int = 0

    @computed_field
    @property
    def score(self) -> float:
        cfg = settings.verification_stability
        raw_score = (
            1.0
            - (self.total_gate_failures / cfg.max_gate_failures)
            - (self.repeat_failures * cfg.repeat_penalty)
        )
        return round(max(0.0, min(1.0, raw_score)), 3)


class CoverageScore(BaseModel):
    """Measured test coverage against a required threshold."""

    threshold: float | None = None
    measured: float | None = None
    source: str | None = None
    passed: bool = True


class RequirementsCoverageScore(BaseModel):
    """Requirement presence and requirement-to-test mapping coverage."""

    total_requirements: int = 0
    satisfied_requirements: int = 0
    mapped_requirements: int = 0
    mapped_satisfied_requirements: int = 0
    missing_requirement_ids: list[str] = Field(default_factory=list)
    requirement_gap_ids: list[str] = Field(default_factory=list)
    requirement_test_evidence_gaps: dict[str, list[str]] = Field(default_factory=dict)

    @computed_field
    @property
    def presence_ratio(self) -> float:
        if self.total_requirements == 0:
            return 1.0
        return self.satisfied_requirements / self.total_requirements

    @computed_field
    @property
    def mapping_ratio(self) -> float:
        if self.total_requirements == 0:
            return 1.0
        return self.mapped_requirements / self.total_requirements


class GateCheck(BaseModel):
    """Single hard-gate validity or performance-gate result."""

    name: str = Field(description="Gate check name")
    passed: bool = Field(description="Whether the check passed")
    evidence: str | None = Field(default=None, description="Check evidence")


class ExecutionValidityScore(BaseModel):
    """Hard-gate execution validity aggregate."""

    checks: list[GateCheck] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        if not self.checks:
            return True
        return all(check.passed for check in self.checks)


class PerformanceGatesScore(BaseModel):
    """Performance gate aggregate for scored scenario outcomes."""

    checks: list[GateCheck] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        if not self.checks:
            return True
        return all(check.passed for check in self.checks)


class ResourceEfficiencyScore(BaseModel):
    """Resource-efficiency metrics used after execution validity succeeds."""

    uncached_input_tokens: int = 0
    output_tokens: int = 0
    command_count: int = 0
    failed_command_count: int = 0
    verification_rounds: int = 0
    repeated_verification_failures: int = 0

    @computed_field
    @property
    def score(self) -> float:
        cfg = settings.resource_efficiency
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


class MetricResult(BaseModel):
    """Audit result for one configured metric."""

    metric_id: str = Field(description="Metric identifier")
    passed: bool = Field(description="Whether the metric evaluation passed")
    matched_count: int = Field(default=0, ge=0, description="Matched artifact count")
    missing_patterns: list[str] = Field(default_factory=list)
    evidence: str | None = Field(default=None, description="Supporting evidence")


class Scorecard(BaseModel):
    """Complete scorecard for an experiment run."""

    run_id: str = ""
    scenario_name: str = ""
    scenario_revision: str = ""
    harness: str = ""
    model: str = ""
    starter_root: str = ""
    duration_sec: float = 0.0
    terminated_early: bool = False
    termination_reason: str | None = None
    unscored: bool = False
    unscored_reasons: list[str] = Field(default_factory=list)
    score_profile: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    functional: FunctionalScore = Field(default_factory=FunctionalScore)
    acceptance: AcceptanceScore = Field(default_factory=AcceptanceScore)
    visual: VisualScore | None = Field(default_factory=VisualScore)
    verification_stability: VerificationStabilityScore = Field(
        default_factory=VerificationStabilityScore
    )
    test_coverage: CoverageScore = Field(default_factory=CoverageScore)
    requirements_coverage: RequirementsCoverageScore = Field(
        default_factory=RequirementsCoverageScore
    )
    execution_validity: ExecutionValidityScore = Field(default_factory=ExecutionValidityScore)
    performance_gates: PerformanceGatesScore = Field(default_factory=PerformanceGatesScore)
    resource_efficiency: ResourceEfficiencyScore = Field(default_factory=ResourceEfficiencyScore)
    metric_results: list[MetricResult] = Field(default_factory=list)

    @computed_field
    @property
    def quality_score(self) -> float:
        weights = settings.weights
        visual_score = self.visual.score if self.visual else 0.0
        if self.visual:
            return (
                self.functional.score * weights.functional
                + self.acceptance.score * weights.acceptance
                + visual_score * weights.visual
                + self.verification_stability.score * weights.verification_stability
            )
        non_visual_total = weights.functional + weights.acceptance + weights.verification_stability
        return (
            self.functional.score * (weights.functional / non_visual_total)
            + self.acceptance.score * (weights.acceptance / non_visual_total)
            + self.verification_stability.score
            * (weights.verification_stability / non_visual_total)
        )

    @computed_field
    @property
    def composite_score(self) -> float:
        if self.unscored:
            return 0.0
        if not self.execution_validity.passed:
            return 0.0
        weights = self.score_profile.get("weights") or {"resource-efficiency": 1.0}
        weighted_score = 0.0
        total_weight = 0.0
        for metric_id, raw_weight in weights.items():
            weight = float(raw_weight)
            if weight <= 0:
                continue
            weighted_score += self._score_profile_metric(str(metric_id)) * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return round(weighted_score / total_weight, 3)

    def _score_profile_metric(self, metric_id: str) -> float:
        """Return the scalar score used by a score-profile metric."""
        metric_scores = {
            "functional": self.functional.score,
            "acceptance": self.acceptance.score,
            "verification-stability": self.verification_stability.score,
            "execution-validity": 1.0 if self.execution_validity.passed else 0.0,
            "resource-efficiency": self.resource_efficiency.score,
            "test-coverage": self._test_coverage_profile_score(),
            "requirements-coverage": self._requirements_coverage_profile_score(),
            "visual-regression": self.visual.score if self.visual else 0.0,
            "artifact-checks": self._artifact_checks_profile_score(),
        }
        return metric_scores.get(metric_id, 0.0)

    def _test_coverage_profile_score(self) -> float:
        if self.test_coverage.threshold is None:
            return 1.0 if self.test_coverage.passed else 0.0
        if self.test_coverage.measured is None:
            return 0.0
        return min(1.0, self.test_coverage.measured / self.test_coverage.threshold)

    def _requirements_coverage_profile_score(self) -> float:
        return (
            self.requirements_coverage.presence_ratio * 0.5
            + self.requirements_coverage.mapping_ratio * 0.5
        )

    def _artifact_checks_profile_score(self) -> float:
        artifact_results = [
            result for result in self.metric_results if result.metric_id == "artifact-checks"
        ]
        if not artifact_results:
            return 0.0
        passed = sum(1 for result in artifact_results if result.passed)
        return passed / len(artifact_results)

    @computed_field
    @property
    def diagnostic_score(self) -> float:
        return round(
            (self.quality_score * 0.6)
            + (self.requirements_coverage.mapping_ratio * 0.25)
            + (self.resource_efficiency.score * 0.15),
            3,
        )


class EvalConfig(BaseModel):
    """Configuration for an experiment run."""

    model: str = Field(description="Model identifier (provider/name)")
    harness: str = Field(description="Harness name")
    scenario_name: str = Field(description="Scenario identifier")
    scenario_revision: str = Field(description="Scenario revision")
    starter_root: str = Field(description="Scenario-local starter root path")
    evaluation_profile: str = Field(description="Metric capability profile identifier")


class EvalRun(BaseModel):
    """Complete experiment run record."""

    id: str = Field(description="Unique run identifier")
    timestamp: str = Field(description="ISO timestamp of run start")
    config: EvalConfig = Field(description="Run configuration")
    duration_sec: float = Field(description="Run duration in seconds")
    terminated_early: bool = Field(default=False, description="Whether run terminated early")
    termination_reason: str | None = Field(default=None, description="Reason for early termination")
    scores: Scorecard = Field(description="Evaluation scores")
    traces: list[TraceEvent] = Field(default_factory=list, description="Execution trace events")
    gate_history: list[GateEvent] = Field(
        default_factory=list, description="Gate execution history"
    )
