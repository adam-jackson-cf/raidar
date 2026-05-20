"""Pydantic models for authored scenario definitions."""

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from raidar.scenario_paths import validate_relative_path

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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    root: str = Field(
        default="starter",
        description="Relative path from scenario revision directory to starter root",
    )


class PromptConfig(BaseModel):
    """Prompt artifact configuration."""

    model_config = ConfigDict(extra="forbid")

    entry: str = Field(
        description="Primary prompt artifact path relative to scenario revision directory"
    )
    includes: list[str] = Field(
        default_factory=list,
        description="Additional prompt artifact paths to append in order",
    )


class DeterministicCheck(BaseModel):
    """Deterministic acceptance check."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["import_present", "file_exists", "no_pattern"] = Field(description="Check type")
    pattern: str = Field(description="Pattern to match")
    description: str = Field(description="Human-readable description")


class QueryRoleTestEvidence(BaseModel):
    """Structured evidence that tests query an element by ARIA role."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["query_role"] = "query_role"
    role: str = Field(description="ARIA role asserted by the test")
    min_count: int = Field(
        default=1,
        ge=1,
        description="Minimum number of qualifying role queries expected across test sources",
    )
    level: int | None = Field(
        default=None,
        ge=1,
        description="Optional heading level or role-specific level constraint",
    )
    name: str | None = Field(
        default=None,
        description="Optional accessible-name hint when a named role query is required",
    )


class QueryTextTestEvidence(BaseModel):
    """Structured evidence that tests query text intentionally."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["query_text"] = "query_text"
    pattern: str = Field(description="Regex-compatible text pattern expected in a text query")
    min_count: int = Field(
        default=1,
        ge=1,
        description="Minimum number of qualifying text queries expected across test sources",
    )


TestEvidenceSpec = Annotated[
    QueryRoleTestEvidence | QueryTextTestEvidence,
    Field(discriminator="type"),
]


class RequirementSpec(BaseModel):
    """Scenario requirement with deterministic presence and optional test evidence checks."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable requirement identifier")
    description: str = Field(description="Requirement description")
    check: DeterministicCheck = Field(description="Deterministic check for requirement presence")
    required_test_evidence: list[TestEvidenceSpec] = Field(
        default_factory=list,
        description="Optional structured evidence that tests cover the requirement conceptually",
    )


class AcceptanceConfig(BaseModel):
    """Acceptance checking configuration."""

    model_config = ConfigDict(extra="forbid")

    deterministic_checks: list[DeterministicCheck] = Field(default_factory=list)
    requirements: list[RequirementSpec] = Field(default_factory=list)


class VisualConfig(BaseModel):
    """Visual regression configuration."""

    model_config = ConfigDict(extra="forbid")

    class VisualBand(BaseModel):
        """Lower/upper scoring band for one visual component."""

        model_config = ConfigDict(extra="forbid")

        lower: float = Field(ge=0, le=1)
        upper: float = Field(ge=0, le=1)

        @model_validator(mode="after")
        def _validate_bounds(self) -> "VisualConfig.VisualBand":
            if self.upper <= self.lower:
                raise ValueError("visual scoring bands require upper > lower")
            return self

    class VisualScoringWeights(BaseModel):
        """Relative weights for the Oracle visual scoring formula."""

        model_config = ConfigDict(populate_by_name=True, extra="forbid")

        global_weight: float = Field(alias="global", gt=0)
        regional: float = Field(gt=0)
        worst_region: float = Field(gt=0)
        region_pass_rate: float = Field(gt=0)

    class VisualScoringBands(BaseModel):
        """Per-component scoring bands for the Oracle visual scoring formula."""

        model_config = ConfigDict(populate_by_name=True, extra="forbid")

        global_band: "VisualConfig.VisualBand" = Field(alias="global")
        regional: "VisualConfig.VisualBand"
        worst_region: "VisualConfig.VisualBand"

    class VisualScoringConfig(BaseModel):
        """Continuous visual score configuration."""

        model_config = ConfigDict(extra="forbid")

        weights: "VisualConfig.VisualScoringWeights"
        bands: "VisualConfig.VisualScoringBands"
        gamma: float = Field(default=2.0, gt=0)
        region_pass_threshold: float = Field(default=0.9, ge=0, le=1)

    class VisualPassPolicy(BaseModel):
        """Hard visual pass/fail and tier thresholds."""

        model_config = ConfigDict(extra="forbid")

        fail_if_global_below: float = Field(default=0.9, ge=0, le=1)
        fail_if_worst_region_below: float = Field(default=0.85, ge=0, le=1)
        minimum_score: float = Field(default=70.0, ge=0, le=100)
        minimum_region_pass_rate: float = Field(default=0.75, ge=0, le=1)
        minimum_worst_region: float = Field(default=0.88, ge=0, le=1)
        high_fidelity_score: float = Field(default=85.0, ge=0, le=100)
        high_fidelity_global: float = Field(default=0.95, ge=0, le=1)
        high_fidelity_worst_region: float = Field(default=0.92, ge=0, le=1)

    class VisualViewport(BaseModel):
        """Viewport used for authored visual captures."""

        model_config = ConfigDict(extra="forbid")

        width: int = Field(gt=0)
        height: int = Field(gt=0)

    class VisualRegionClip(BaseModel):
        """Viewport clip for one authored visual region."""

        model_config = ConfigDict(extra="forbid")

        x: int = Field(ge=0)
        y: int = Field(ge=0)
        width: int = Field(gt=0)
        height: int = Field(gt=0)

    class VisualRegion(BaseModel):
        """One authored visual region used for capture and scoring."""

        model_config = ConfigDict(extra="forbid")

        name: str = Field(description="Stable authored region name")
        weight: float = Field(default=1.0, gt=0, description="Relative scoring weight")
        clip: "VisualConfig.VisualRegionClip" = Field(description="Viewport clip rectangle")

    reference_image: str = Field(description="Path to reference image")
    screenshot_command: list[str] = Field(
        default_factory=lambda: ["bun", "run", "capture-screenshot"],
        min_length=1,
        description="Command argv to capture screenshot",
    )
    viewport: "VisualConfig.VisualViewport | None" = Field(
        default=None,
        description="Authored viewport used for screenshot capture",
    )
    scoring: "VisualConfig.VisualScoringConfig" = Field(
        description="Continuous scoring configuration for visual comparison",
    )
    pass_policy: "VisualConfig.VisualPassPolicy" = Field(
        description="Visual pass/fail thresholds and tiers",
    )
    regions: list[VisualRegion] = Field(
        default_factory=list,
        description="Authored visual regions for local capture and scoring",
    )

    @field_validator("screenshot_command")
    @classmethod
    def _validate_screenshot_command(cls, value: list[str]) -> list[str]:
        return _validate_argv_command(value, field_name="visual.screenshot_command")


class VerificationConfig(BaseModel):
    """Verification configuration."""

    model_config = ConfigDict(extra="forbid")

    class VerificationWorkflowConfig(BaseModel):
        """Workflow requirements applied outside the task prompt."""

        model_config = ConfigDict(extra="forbid")

        atomic_commits_required: bool = Field(
            default=False,
            description="Require at least one atomic git commit before completion is valid",
        )

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
    setup_actions: list[list[str]] = Field(
        default_factory=list,
        description="Workspace setup commands to execute before verification gates",
    )
    gates: list[VerificationGate] = Field(default_factory=list)
    workflow: "VerificationConfig.VerificationWorkflowConfig" = Field(
        default_factory=VerificationWorkflowConfig
    )

    @field_validator("required_commands")
    @classmethod
    def _validate_required_commands(cls, value: list[list[str]]) -> list[list[str]]:
        return [
            _validate_argv_command(command, field_name="verification.required_commands[]")
            for command in value
        ]

    @field_validator("setup_actions")
    @classmethod
    def _validate_setup_actions(cls, value: list[list[str]]) -> list[list[str]]:
        return [
            _validate_argv_command(command, field_name="verification.setup_actions[]")
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
    "visual-regression",
]

MetricId = Literal[
    "functional",
    "acceptance",
    "verification-stability",
    "execution-validity",
    "resource-efficiency",
    "test-coverage",
    "requirements-coverage",
    "plan-quality",
    "visual-regression",
    "artifact-checks",
]


class CoreMetricDefinition(BaseModel):
    """Built-in metric definition."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["core"] = "core"
    id: CoreMetricId = Field(description="Core metric id")


class ArtifactCheckMetricConfig(BaseModel):
    """Configuration for artifact checks."""

    model_config = ConfigDict(extra="forbid")

    required_paths: list[str] = Field(
        min_length=1,
        description="Glob paths that must exist in the run workspace",
    )
    path_match: Literal["glob"] = Field(default="glob", description="Path matching mode")


class ArtifactCheckMetricDefinition(BaseModel):
    """Metric definition that checks required artifacts exist."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["artifact-checks"] = "artifact-checks"
    id: Literal["artifact-checks"] = "artifact-checks"
    config: ArtifactCheckMetricConfig = Field(description="Artifact check configuration")


class LLMAsJudgeMetricConfig(BaseModel):
    """Configuration for an LLM-as-judge metric."""

    model_config = ConfigDict(extra="forbid")

    judge: str = Field(description="Path to a judge role file relative to scorer definitions")

    @field_validator("judge")
    @classmethod
    def _validate_judge_path(cls, value: str) -> str:
        return validate_relative_path(
            value,
            field_name="llm-as-judge.config.judge",
            root_name="scorer definitions",
        )


class LLMAsJudgeMetricDefinition(BaseModel):
    """Metric definition that evaluates output with a judge role file."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["llm-as-judge"] = "llm-as-judge"
    id: Literal["plan-quality"] = "plan-quality"
    config: LLMAsJudgeMetricConfig = Field(description="LLM judge configuration")


MetricDefinition = Annotated[
    CoreMetricDefinition | ArtifactCheckMetricDefinition | LLMAsJudgeMetricDefinition,
    Field(discriminator="type"),
]


class ScorerMetricDefinition(BaseModel):
    """Weighted metric entry inside a reusable scorer definition."""

    model_config = ConfigDict(extra="forbid")

    id: MetricId = Field(description="Metric identifier")
    type: Literal["core", "artifact-checks", "llm-as-judge"] = Field(
        description="Metric implementation type"
    )
    weight: float = Field(gt=0, description="Metric weight inside this scorer")
    config: dict[str, Any] = Field(default_factory=dict)


class ScenarioScorerRef(BaseModel):
    """Scenario reference to a reusable scorer definition."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Scorer identifier")
    version: int = Field(ge=1, description="Scorer definition version")
    weight: float = Field(gt=0, description="Scenario-level scorer weight")
    config: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Scenario-level metric config overrides keyed by metric id",
    )


class ScenarioDefinition(BaseModel):
    """Complete scenario definition matching the YAML format."""

    model_config = ConfigDict(extra="forbid")

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
    scorers: list[ScenarioScorerRef] = Field(
        min_length=1,
        description="Reusable scorer definitions attached to this scenario",
    )
    prompt: PromptConfig = Field(description="Prompt artifact configuration")

    def metric_ids(self) -> list[str]:
        """Return ordered metric ids derived from attached scorers."""
        return [metric.id for metric in self.resolved_metrics()]

    def scorer_ids(self) -> list[str]:
        """Return deterministic scorer references."""
        return [f"{scorer.id}@{scorer.version}" for scorer in self.scorers]

    def resolved_scorers(self):
        """Return validated scorer definitions with scenario config merged."""
        from raidar.scorers.registry import resolve_scorers

        return resolve_scorers(self)

    def resolved_metrics(self) -> list[MetricDefinition]:
        """Return de-duplicated metric definitions derived from resolved scorers."""
        from raidar.scorers.registry import resolved_metrics

        return resolved_metrics(self)

    @model_validator(mode="after")
    def _validate_scorers(self) -> "ScenarioDefinition":
        if len(self.scorer_ids()) != len(set(self.scorer_ids())):
            raise ValueError("scorers contains duplicate scorer references")
        self.resolved_scorers()
        metric_ids = self.metric_ids()
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("resolved scorers contain duplicate metric ids")
        self._validate_metric_dependencies(metric_ids)
        return self

    def _validate_metric_dependencies(self, metric_ids: list[str]) -> None:
        if "test-coverage" in metric_ids and self.verification.coverage_threshold is None:
            raise ValueError(
                "metrics includes test-coverage without verification.coverage_threshold"
            )
        if "requirements-coverage" in metric_ids and not self.acceptance.requirements:
            raise ValueError(
                "metrics includes requirements-coverage without acceptance.requirements"
            )
        if "visual-regression" in metric_ids and self.visual is None:
            raise ValueError("scorers include visual-regression without visual config")
        has_quality_scorer = any(scorer.category == "quality" for scorer in self.resolved_scorers())
        if self.verification.min_quality_score > 0 and not has_quality_scorer:
            raise ValueError("verification.min_quality_score requires at least one quality scorer")

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
