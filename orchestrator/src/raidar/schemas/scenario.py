"""Pydantic models for authored scenario definitions."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from raidar.scenario_paths import validate_relative_path
from raidar.schemas.environment import (
    CapabilityRequirements,
    EnvironmentConfig,
    EnvironmentResourcesConfig,
)

__all__ = [
    "CapabilityRequirements",
    "EnvironmentConfig",
    "EnvironmentResourcesConfig",
]

SHELL_WRAPPER_ARGS = {"-c", "-lc", "/c", "/k", "-command", "-encodedcommand"}
TaskKind = Literal[
    "feature_implementation",
    "design_implementation",
    "defect_repair",
    "refactoring",
    "test_authoring",
]
DeliveryMode = Literal["direct_implementation", "plan_driven", "plan_and_implement"]
WorkspaceState = Literal["new", "existing"]
StartingCondition = Literal["known_defect", "legacy_structure"]
EvaluationRole = Literal["benchmark", "operational"]
CatalogStatus = Literal["active", "calibration", "quarantined", "retired"]


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
    """Deterministic requirement check."""

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


class RequirementsConfig(BaseModel):
    """Scenario requirements configuration."""

    model_config = ConfigDict(extra="forbid")

    items: list[RequirementSpec] = Field(default_factory=list)


class RetainedEvidenceFile(BaseModel):
    """One JSON evidence file the agent must retain in the run workspace."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="Workspace-relative path to a JSON object file written during the run"
    )
    description: str = Field(
        default="",
        description="What evidence the file is expected to retain",
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(
            value,
            field_name="evidence.retained_files[].path",
            root_name="run workspace",
        )


class EvidenceConfig(BaseModel):
    """Scenario-declared retained-evidence contract."""

    model_config = ConfigDict(extra="forbid")

    retained_files: list[RetainedEvidenceFile] = Field(
        default_factory=list,
        description=(
            "JSON evidence files ingested from the final run workspace into scorer-visible "
            "retained evidence"
        ),
    )


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
        reference_image: str = Field(
            description="Scenario-relative reference image path for this region"
        )
        actual_image: str = Field(
            description="Workspace-relative actual capture path for this region"
        )
        diff_image: str = Field(
            description="Workspace-relative visual diff output path for this region"
        )
        weight: float = Field(default=1.0, gt=0, description="Relative scoring weight")
        clip: "VisualConfig.VisualRegionClip" = Field(description="Viewport clip rectangle")

        @field_validator("reference_image", "actual_image", "diff_image")
        @classmethod
        def _validate_region_path(cls, value: str) -> str:
            return validate_relative_path(
                value,
                field_name="visual.regions[] image path",
                root_name="scenario or workspace",
            )

    class VisualArtifactManifest(BaseModel):
        """Scenario-owned output paths produced by visual capture/scoring."""

        model_config = ConfigDict(extra="forbid")

        actual_image: str = Field(description="Workspace-relative captured full-page image")
        diff_image: str = Field(description="Workspace-relative full-page diff output")
        post_capture_image: str = Field(
            description="Run-artifact relative screenshot captured after the agent run"
        )

        @field_validator("actual_image", "diff_image", "post_capture_image")
        @classmethod
        def _validate_manifest_path(cls, value: str) -> str:
            return validate_relative_path(
                value,
                field_name="visual.artifact_manifest image path",
                root_name="visual artifact",
            )

    reference_image: str = Field(description="Path to reference image")
    capture_setup_actions: list[list[str]] = Field(
        default_factory=list,
        description="Workspace setup commands to execute before visual capture",
    )
    screenshot_command: list[str] = Field(
        min_length=1,
        description="Command argv to capture screenshot",
    )
    artifact_manifest: VisualArtifactManifest = Field(
        description="Scenario-owned visual capture and diff output paths"
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

    @field_validator("capture_setup_actions")
    @classmethod
    def _validate_capture_setup_actions(cls, value: list[list[str]]) -> list[list[str]]:
        return [
            _validate_argv_command(command, field_name="visual.capture_setup_actions[]")
            for command in value
        ]


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
    preflight_command_timeout_sec: int | None = Field(
        default=None,
        gt=0,
        description="Optional timeout for preflight setup and required command checks",
    )
    test_discovery_globs: list[str] = Field(
        default_factory=list,
        description="Workspace-relative glob patterns used to decide whether test commands exist",
    )
    skip_test_commands_when_no_tests: bool = Field(
        default=False,
        description="Whether preflight may skip test commands when discovery globs match no files",
    )
    skip_test_commands_in_starter_preflight: bool = Field(
        default=False,
        description="Whether preflight skips test commands even when the starter contains tests",
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

    @field_validator("test_discovery_globs")
    @classmethod
    def _validate_test_discovery_globs(cls, value: list[str]) -> list[str]:
        return [
            validate_relative_path(
                pattern,
                field_name="verification.test_discovery_globs[]",
                root_name="workspace",
            )
            for pattern in value
        ]


CoreMetricId = Literal[
    "functional",
    "code-quality",
    "verification-stability",
    "execution-validity",
    "resource-efficiency",
    "test-coverage",
    "requirements-coverage",
    "visual-regression",
    "defect-resolution",
    "regression-protection",
    "change-containment",
    "defect-evidence-completeness",
    "behavior-preservation",
    "structural-improvement",
    "public-contract-stability",
    "planned-scope-coverage",
    "acceptance-evidence-completeness",
    "requirement-mapping",
    "assertion-strength",
    "coverage-lift",
    "production-code-guardrail",
    "bugfix-defect-resolution-v2",
    "bugfix-regression-protection-v2",
    "bugfix-change-containment-v2",
    "bugfix-verification-stability-v2",
    "bugfix-defect-evidence-completeness-v2",
    "refactor-behavior-preservation-v2",
    "refactor-structural-improvement-v2",
    "refactor-public-contract-stability-v2",
    "refactor-change-containment-v2",
    "refactor-verification-stability-v2",
    "test-generation-requirement-mapping-v2",
    "test-generation-assertion-strength-v2",
    "test-generation-coverage-lift-v2",
    "test-generation-production-code-guardrail-v2",
    "test-generation-verification-stability-v2",
    "plan-to-code-planned-scope-coverage-v2",
    "plan-to-code-acceptance-evidence-completeness-v2",
    "plan-to-code-functional-v2",
    "plan-to-code-verification-stability-v2",
]

MetricId = Literal[
    "functional",
    "code-quality",
    "verification-stability",
    "execution-validity",
    "resource-efficiency",
    "test-coverage",
    "requirements-coverage",
    "requirements-adherence",
    "plan-adherence",
    "visual-regression",
    "artifact-checks",
    "defect-resolution",
    "regression-protection",
    "change-containment",
    "defect-evidence-completeness",
    "behavior-preservation",
    "structural-improvement",
    "public-contract-stability",
    "planned-scope-coverage",
    "acceptance-evidence-completeness",
    "requirement-mapping",
    "assertion-strength",
    "coverage-lift",
    "production-code-guardrail",
    "bugfix-defect-resolution-v2",
    "bugfix-regression-protection-v2",
    "bugfix-change-containment-v2",
    "bugfix-verification-stability-v2",
    "bugfix-defect-evidence-completeness-v2",
    "refactor-behavior-preservation-v2",
    "refactor-structural-improvement-v2",
    "refactor-public-contract-stability-v2",
    "refactor-change-containment-v2",
    "refactor-verification-stability-v2",
    "test-generation-requirement-mapping-v2",
    "test-generation-assertion-strength-v2",
    "test-generation-coverage-lift-v2",
    "test-generation-production-code-guardrail-v2",
    "test-generation-verification-stability-v2",
    "plan-to-code-plan-adherence-v2",
    "plan-to-code-planned-scope-coverage-v2",
    "plan-to-code-acceptance-evidence-completeness-v2",
    "plan-to-code-functional-v2",
    "plan-to-code-verification-stability-v2",
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
    id: Literal[
        "plan-adherence",
        "requirements-adherence",
        "plan-to-code-plan-adherence-v2",
    ]
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
    evidence: str = Field(min_length=1, description="Evidence consumed by this metric")
    score_derivation: str = Field(
        min_length=1,
        description="How the metric's 0..1 score is derived",
    )
    pass_fail: str = Field(min_length=1, description="What makes the metric pass or fail")
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
    display_name: str = Field(min_length=1, description="Human-readable scenario name")
    scenario_revision: str = Field(description="Scenario revision identifier (for example v001)")
    parent_revision: str | None = Field(
        default=None,
        description="Parent scenario revision identifier, or null for a root revision",
    )
    description: str = Field(description="Scenario description")
    difficulty: Literal["easy", "medium", "hard"] = Field(default="medium")
    task_kind: TaskKind = Field(description="Primary work requested from the agent")
    delivery_mode: DeliveryMode = Field(description="How work is delivered to the agent")
    workspace_state: WorkspaceState = Field(description="Whether the workspace is new or existing")
    starting_conditions: list[StartingCondition] = Field(
        default_factory=list,
        description="Known conditions present when the task begins",
    )
    languages: list[str] = Field(
        min_length=1,
        description="Programming languages materially involved in the task",
    )
    evaluation_role: EvaluationRole = Field(description="Scenario evaluation purpose")
    catalog_status: CatalogStatus = Field(description="Scenario catalogue lifecycle status")
    family_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional identifier grouping related scenario variants",
    )
    variant_axes: dict[str, str] = Field(
        default_factory=dict,
        description="Named dimensions distinguishing this scenario variant",
    )
    timeout_sec: int = Field(default=1800, description="Scenario timeout in seconds")
    test_scripts: list[str] = Field(default_factory=list)
    environment: EnvironmentConfig = Field(description="Scenario execution environment contract")
    starter: StarterConfig = Field(description="Starter configuration")
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    requirements: RequirementsConfig = Field(default_factory=RequirementsConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    visual: VisualConfig | None = Field(default=None)
    scorers: list[ScenarioScorerRef] = Field(
        min_length=1,
        description="Reusable scorer definitions attached to this scenario",
    )
    prompt: PromptConfig = Field(description="Prompt artifact configuration")

    @field_validator("display_name", "family_id")
    @classmethod
    def _validate_taxonomy_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("taxonomy text values must not be blank")
        return value

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, languages: list[str]) -> list[str]:
        if any(not language.strip() for language in languages):
            raise ValueError("languages must not contain blank values")
        if len(languages) != len(set(languages)):
            raise ValueError("languages must not contain duplicates")
        return languages

    @field_validator("starting_conditions")
    @classmethod
    def _validate_starting_conditions(
        cls, starting_conditions: list[StartingCondition]
    ) -> list[StartingCondition]:
        if len(starting_conditions) != len(set(starting_conditions)):
            raise ValueError("starting_conditions must not contain duplicates")
        return starting_conditions

    @field_validator("variant_axes")
    @classmethod
    def _validate_variant_axes(cls, variant_axes: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not value.strip() for key, value in variant_axes.items()):
            raise ValueError("variant_axes keys and values must not be blank")
        return variant_axes

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
    def _validate_taxonomy_consistency(self) -> "ScenarioDefinition":
        conditions = set(self.starting_conditions)
        self._validate_defect_repair_conditions(conditions)
        self._validate_existing_workspace_conditions(conditions)
        self._validate_new_workspace_conditions(conditions)
        self._validate_family_variant_coupling()
        return self

    def _validate_defect_repair_conditions(self, conditions: set[StartingCondition]) -> None:
        if self.task_kind == "defect_repair" and "known_defect" not in conditions:
            raise ValueError("defect_repair requires starting_conditions to include known_defect")
        if "known_defect" in conditions and self.task_kind != "defect_repair":
            raise ValueError("known_defect requires task_kind to be defect_repair")

    def _validate_existing_workspace_conditions(self, conditions: set[StartingCondition]) -> None:
        if "legacy_structure" in conditions and self.workspace_state != "existing":
            raise ValueError("legacy_structure requires workspace_state to be existing")

    def _validate_new_workspace_conditions(self, conditions: set[StartingCondition]) -> None:
        if self.workspace_state == "new" and conditions:
            raise ValueError("new workspace_state must not declare starting_conditions")

    def _validate_family_variant_coupling(self) -> None:
        if self.variant_axes and self.family_id is None:
            raise ValueError("variant_axes requires family_id")

    @model_validator(mode="after")
    def _validate_scorers(self) -> "ScenarioDefinition":
        if self.parent_revision == self.scenario_revision:
            raise ValueError("parent_revision must differ from scenario_revision")
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
        if "requirements-coverage" in metric_ids and not self.requirements.items:
            raise ValueError("metrics includes requirements-coverage without requirements.items")
        if "visual-regression" in metric_ids and self.visual is None:
            raise ValueError("scorers include visual-regression without visual config")
        has_quality_scorer = any(scorer.category == "quality" for scorer in self.resolved_scorers())
        if self.verification.min_quality_score > 0 and not has_quality_scorer:
            raise ValueError("verification.min_quality_score requires at least one quality scorer")
