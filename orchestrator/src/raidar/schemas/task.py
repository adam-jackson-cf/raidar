"""Pydantic models for task definition."""

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

SHELL_WRAPPER_ARGS = {"-c", "-lc", "/c", "/k", "-command", "-encodedcommand"}
SHELL_WRAPPER_BINARIES = {
    "bash",
    "sh",
    "zsh",
    "fish",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}
SHELL_OPERATOR_PATTERN = re.compile(r"&&|\|\||[|;]|>>?|<<|`|\$\(|\${")


def _validate_argv_command(argv: list[str], *, field_name: str) -> list[str]:
    """Reject shell wrappers and shell operators in task YAML commands."""
    if not argv:
        return argv
    binary = argv[0].strip().lower()
    if binary in SHELL_WRAPPER_BINARIES:
        raise ValueError(
            f"{field_name} must be an argv list for the target command, not a shell wrapper"
        )
    for part in argv:
        if SHELL_OPERATOR_PATTERN.search(part):
            raise ValueError(
                f"{field_name} must not include shell operators or shell features; "
                f"found forbidden token in {part!r}"
            )
    lowered = {part.strip().lower() for part in argv[1:]}
    if lowered & SHELL_WRAPPER_ARGS:
        raise ValueError(
            f"{field_name} must not invoke a command through shell flags like -c or -lc"
        )
    return argv


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

    @field_validator("command")
    @classmethod
    def _validate_command_argv(cls, value: list[str]) -> list[str]:
        return _validate_argv_command(value, field_name="verification.gates[].command")


class ScaffoldConfig(BaseModel):
    """Task-local scaffold configuration."""

    root: str = Field(
        default="scaffold",
        description="Relative path (from task version directory) to scaffold root",
    )


class PromptConfig(BaseModel):
    """Prompt artifact configuration."""

    entry: str = Field(
        description="Primary prompt artifact path relative to task version directory"
    )
    includes: list[str] = Field(
        default_factory=list,
        description="Additional prompt artifact paths to append in order",
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

    @field_validator("screenshot_command")
    @classmethod
    def _validate_screenshot_command(cls, value: list[str]) -> list[str]:
        return _validate_argv_command(value, field_name="visual.screenshot_command")


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

    @field_validator("required_commands")
    @classmethod
    def _validate_required_commands(cls, value: list[list[str]]) -> list[list[str]]:
        return [
            _validate_argv_command(command, field_name="verification.required_commands[]")
            for command in value
        ]


CoreMetricModuleId = Literal[
    "functional",
    "compliance",
    "efficiency",
    "run-validity",
    "optimization",
    "coverage-threshold",
    "requirements",
    "llm-judge",
    "visual-odiff",
]


class CoreMetricModule(BaseModel):
    """Built-in metric module descriptor."""

    type: Literal["core"] = "core"
    id: CoreMetricModuleId = Field(description="Core metric module id")


class ArtifactPresenceMetricConfig(BaseModel):
    """Configuration for artifact presence checks."""

    required_paths: list[str] = Field(
        min_length=1,
        description="Glob paths that must exist in the run workspace",
    )
    path_match: Literal["glob"] = Field(
        default="glob",
        description="Path matching mode",
    )


class ArtifactPresenceMetricModule(BaseModel):
    """Metric module that checks required artifacts exist."""

    type: Literal["artifact_presence"] = "artifact_presence"
    id: Literal["artifact_presence"] = "artifact_presence"
    config: ArtifactPresenceMetricConfig = Field(
        description="Artifact presence module configuration"
    )


MetricModule = Annotated[
    CoreMetricModule | ArtifactPresenceMetricModule,
    Field(discriminator="type"),
]


class MetricsConfig(BaseModel):
    """Metrics module configuration."""

    modules: list[MetricModule] = Field(
        min_length=1,
        description="Ordered metric modules enabled for this task",
    )


class TaskDefinition(BaseModel):
    """Complete task definition matching the YAML format."""

    name: str = Field(description="Task identifier")
    version: str = Field(description="Task version identifier (e.g., v001)")
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
    metrics: MetricsConfig = Field(description="Metric module configuration")

    # Prompt artifacts
    prompt: PromptConfig = Field(description="Prompt artifact configuration")

    def metric_module_ids(self) -> list[str]:
        """Return ordered metric module ids."""
        return [module.id for module in self.metrics.modules]

    @model_validator(mode="after")
    def _validate_metrics_modules(self) -> "TaskDefinition":
        module_ids = self.metric_module_ids()
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("metrics.modules contains duplicate module ids")
        if "coverage-threshold" in module_ids and self.verification.coverage_threshold is None:
            raise ValueError(
                "metrics.modules includes coverage-threshold "
                "without verification.coverage_threshold"
            )
        if "requirements" in module_ids and not self.compliance.requirements:
            raise ValueError(
                "metrics.modules includes requirements without compliance.requirements"
            )
        if "llm-judge" in module_ids and not self.compliance.llm_judge_rubric:
            raise ValueError(
                "metrics.modules includes llm-judge without compliance.llm_judge_rubric"
            )
        if "visual-odiff" in module_ids and self.visual is None:
            raise ValueError("metrics.modules includes visual-odiff without visual config")
        return self

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
