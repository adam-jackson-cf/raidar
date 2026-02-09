"""Pydantic models for task definition.

Extends Harbor's YAML format with custom fields for compliance, visual, and efficiency scoring.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class VerificationGate(BaseModel):
    """Configuration for a verification gate."""

    name: str = Field(description="Gate identifier (typecheck, lint, test)")
    command: list[str] = Field(
        min_length=1,
        description="Command argv to execute",
    )
    on_failure: Literal["continue", "terminate"] = Field(
        default="continue",
        description="Action when gate fails",
    )


class ScaffoldConfig(BaseModel):
    """Scaffold template configuration."""

    template: str = Field(description="Scaffold template name")
    version: str = Field(description="Template version identifier")
    rules_variant: Literal["strict", "minimal", "none"] = Field(
        default="strict",
        description="Rules variant to inject",
    )


class DeterministicCheck(BaseModel):
    """Deterministic compliance check."""

    type: Literal["import_present", "file_exists", "no_pattern"] = Field(description="Check type")
    pattern: str = Field(description="Pattern to match")
    description: str = Field(description="Human-readable description")


class RequirementSpec(BaseModel):
    """Task requirement with deterministic presence and test mapping checks."""

    id: str = Field(description="Stable requirement identifier")
    description: str = Field(description="Requirement description")
    check: DeterministicCheck = Field(description="Deterministic check for requirement presence")
    required_test_patterns: list[str] = Field(
        default_factory=list,
        description="Patterns that must appear in test sources to satisfy test mapping",
    )


class LLMJudgeCriterion(BaseModel):
    """LLM judge evaluation criterion."""

    criterion: str = Field(description="Evaluation criterion description")
    weight: float = Field(ge=0, le=1, description="Weight for this criterion")


class ComplianceConfig(BaseModel):
    """Compliance checking configuration."""

    deterministic_checks: list[DeterministicCheck] = Field(default_factory=list)
    requirements: list[RequirementSpec] = Field(default_factory=list)
    llm_judge_rubric: list[LLMJudgeCriterion] = Field(default_factory=list)


class VisualConfig(BaseModel):
    """Visual regression configuration."""

    reference_image: str = Field(description="Path to reference image")
    screenshot_command: list[str] = Field(
        default_factory=lambda: ["bun", "run", "capture-screenshot"],
        min_length=1,
        description="Command argv to capture screenshot",
    )
    threshold: float = Field(
        default=0.95,
        ge=0,
        le=1,
        description="Minimum similarity threshold",
    )


class VerificationConfig(BaseModel):
    """Verification configuration."""

    max_gate_failures: int = Field(
        default=3,
        description="Maximum gate failures before termination",
    )
    coverage_threshold: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Minimum required test coverage ratio (0-1)",
    )
    min_quality_score: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="Minimum quality score required before optimization ranking applies",
    )
    required_commands: list[list[str]] = Field(
        default_factory=list,
        description="Verification commands the agent must execute during the task run",
    )
    gates: list[VerificationGate] = Field(default_factory=list)


class TaskDefinition(BaseModel):
    """Complete task definition matching the YAML format."""

    name: str = Field(description="Task identifier")
    description: str = Field(description="Task description")
    difficulty: Literal["easy", "medium", "hard"] = Field(default="medium")
    category: str = Field(description="Task category (greenfield-ui, etc)")
    timeout_sec: int = Field(default=1800, description="Task timeout in seconds")

    # Harbor fields
    dockerfile: str = Field(default="./Dockerfile")
    test_scripts: list[str] = Field(default_factory=list)

    # Custom eval fields
    scaffold: ScaffoldConfig = Field(description="Scaffold configuration")
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    visual: VisualConfig | None = Field(default=None)

    # Task prompt
    prompt: str = Field(description="Task prompt shown to the agent")

    @classmethod
    def from_yaml(cls, path: Path) -> "TaskDefinition":
        """Load task definition from a YAML file."""
        with path.open() as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        """Save task definition to a YAML file."""
        with path.open("w") as f:
            yaml.dump(self.model_dump(exclude_none=True), f, sort_keys=False)
