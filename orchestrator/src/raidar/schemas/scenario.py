"""Pydantic models for authored scenario definitions."""

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
    """Reject shell wrappers and shell operators in scenario YAML commands."""
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
    command: list[str] = Field(min_length=1, description="Command argv to execute")
    on_failure: Literal["continue", "terminate"] = Field(
        default="continue",
        description="Action when gate fails",
    )

    @field_validator("command")
    @classmethod
    def _validate_command_argv(cls, value: list[str]) -> list[str]:
        return _validate_argv_command(value, field_name="verification.gates[].command")


class StarterConfig(BaseModel):
    """Scenario-local starter configuration."""

    root: str = Field(
        default="starter",
        description="Relative path from scenario revision directory to starter root",
    )


class PromptConfig(BaseModel):
    """Prompt artifact configuration."""

    entry: str = Field(
        description="Primary prompt artifact path relative to scenario revision directory"
    )
    includes: list[str] = Field(
        default_factory=list,
        description="Additional prompt artifact paths to append in order",
    )


class DeterministicCheck(BaseModel):
    """Deterministic acceptance check."""

    type: Literal["import_present", "file_exists", "no_pattern"] = Field(description="Check type")
    pattern: str = Field(description="Pattern to match")
    description: str = Field(description="Human-readable description")


class RequirementSpec(BaseModel):
    """Scenario requirement with deterministic presence and test mapping checks."""

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


class AcceptanceConfig(BaseModel):
    """Acceptance checking configuration."""

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
        description="Minimum quality score required before resource ranking applies",
    )
    required_commands: list[list[str]] = Field(
        default_factory=list,
        description=(
            "Verification commands the selected AgentSpec must execute during the scenario run"
        ),
    )
    gates: list[VerificationGate] = Field(default_factory=list)

    @field_validator("required_commands")
    @classmethod
    def _validate_required_commands(cls, value: list[list[str]]) -> list[list[str]]:
        return [
            _validate_argv_command(command, field_name="verification.required_commands[]")
            for command in value
        ]


CoreMetricId = Literal[
    "functional",
    "acceptance",
    "verification-stability",
    "execution-validity",
    "resource-efficiency",
    "test-coverage",
    "requirements-coverage",
    "llm-judge",
    "visual-regression",
]


class CoreMetricDefinition(BaseModel):
    """Built-in metric definition."""

    type: Literal["core"] = "core"
    id: CoreMetricId = Field(description="Core metric id")


class ArtifactCheckMetricConfig(BaseModel):
    """Configuration for artifact checks."""

    required_paths: list[str] = Field(
        min_length=1,
        description="Glob paths that must exist in the run workspace",
    )
    path_match: Literal["glob"] = Field(default="glob", description="Path matching mode")


class ArtifactCheckMetricDefinition(BaseModel):
    """Metric definition that checks required artifacts exist."""

    type: Literal["artifact-checks"] = "artifact-checks"
    id: Literal["artifact-checks"] = "artifact-checks"
    config: ArtifactCheckMetricConfig = Field(description="Artifact check configuration")


MetricDefinition = Annotated[
    CoreMetricDefinition | ArtifactCheckMetricDefinition,
    Field(discriminator="type"),
]


class ScenarioDefinition(BaseModel):
    """Complete scenario definition matching the YAML format."""

    name: str = Field(description="Scenario identifier")
    scenario_revision: str = Field(description="Scenario revision identifier (for example v001)")
    description: str = Field(description="Scenario description")
    difficulty: Literal["easy", "medium", "hard"] = Field(default="medium")
    category: str = Field(description="Scenario category (greenfield-ui, etc)")
    timeout_sec: int = Field(default=1800, description="Scenario timeout in seconds")
    dockerfile: str = Field(default="./Dockerfile")
    test_scripts: list[str] = Field(default_factory=list)
    starter: StarterConfig = Field(description="Starter configuration")
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    acceptance: AcceptanceConfig = Field(default_factory=AcceptanceConfig)
    visual: VisualConfig | None = Field(default=None)
    metrics: list[MetricDefinition] = Field(
        min_length=1,
        description="Ordered metric definitions enabled for this scenario",
    )
    prompt: PromptConfig = Field(description="Prompt artifact configuration")

    def metric_ids(self) -> list[str]:
        """Return ordered metric ids."""
        return [metric.id for metric in self.metrics]

    @model_validator(mode="after")
    def _validate_metrics(self) -> "ScenarioDefinition":
        metric_ids = self.metric_ids()
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metrics contains duplicate metric ids")
        if "test-coverage" in metric_ids and self.verification.coverage_threshold is None:
            raise ValueError(
                "metrics includes test-coverage without verification.coverage_threshold"
            )
        if "requirements-coverage" in metric_ids and not self.acceptance.requirements:
            raise ValueError(
                "metrics includes requirements-coverage without acceptance.requirements"
            )
        if "llm-judge" in metric_ids and not self.acceptance.llm_judge_rubric:
            raise ValueError("metrics includes llm-judge without acceptance.llm_judge_rubric")
        if "visual-regression" in metric_ids and self.visual is None:
            raise ValueError("metrics includes visual-regression without visual config")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "ScenarioDefinition":
        """Load scenario definition from a YAML file."""
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return cls.model_validate(data)

    def to_yaml(self, path: Path) -> None:
        """Save scenario definition to a YAML file."""
        with path.open("w", encoding="utf-8") as handle:
            yaml.dump(self.model_dump(exclude_none=True), handle, sort_keys=False)
