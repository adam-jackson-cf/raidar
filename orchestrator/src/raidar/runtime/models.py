"""Typed contracts shared across runtime execution phases."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raidar.agents.config import AgentSpec
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import (
    AcceptanceScore,
    CoverageScore,
    ExecutionValidityScore,
    FunctionalScore,
    MetricResult,
    PerformanceGatesScore,
    RequirementsCoverageScore,
    VerificationStabilityScore,
    VisualScore,
)
from raidar.starter import StarterSource


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Input bundle for running a scenario."""

    scenario: ScenarioDefinition
    config: AgentSpec
    scenario_dir: Path
    execution_dir: Path
    repeat_index: int = 1


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Resolved starter context for a scenario run."""

    starter_source: StarterSource
    baseline_workspace: Path
    baseline_cache_key: str
    baseline_cache_status: str
    baseline_cache_hit: bool
    baseline_metadata_path: Path
    baseline_fingerprint: str
    workspace: Path
    injected_rules: Path | None
    metadata_path: Path


@dataclass(frozen=True, slots=True)
class EvaluationOutputs:
    """Computed scoring outputs for a run."""

    functional: FunctionalScore
    acceptance: AcceptanceScore
    visual: VisualScore | None
    verification_stability: VerificationStabilityScore
    test_coverage: CoverageScore
    requirements_coverage: RequirementsCoverageScore
    execution_validity: ExecutionValidityScore
    performance_gates: PerformanceGatesScore
    metric_results: list[MetricResult]
    gate_history: list[GateEvent]


@dataclass(frozen=True, slots=True)
class HarborExecutionResult:
    """Outcome of the Harbor execution phase."""

    terminated_early: bool
    termination_reason: str | None
    job_dir: Path
    trial_dir: Path | None


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """Normalized command execution record from Codex logs."""

    command: str
    failed: bool
    output: str
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessMetrics:
    """Process metrics extracted from Harbor harness logs."""

    uncached_input_tokens: int
    output_tokens: int
    command_count: int
    failed_command_count: int
    process_failed_command_count: int
    verification_rounds: int
    repeated_verification_failures: int
    required_verification_commands: int
    executed_required_verification_commands: int
    failed_command_categories: dict[str, int] = field(default_factory=dict)
    required_verification_first_pass: dict[str, str] = field(default_factory=dict)
    first_pass_verification_successes: int = 0
    first_pass_verification_failures: int = 0
    missing_required_verification_commands: int = 0
    git_commit_verification_bypass_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunLayout:
    """Filesystem layout for a canonical evaluation run directory."""

    run_id: str
    start_time: datetime
    run_label: str
    root_dir: Path
    workspace_dir: Path
    verifier_dir: Path
    harness_dir: Path
    harbor_dir: Path
    run_json_path: Path
    report_path: Path


@dataclass(frozen=True, slots=True)
class HarborExecutionRequest:
    """Typed Harbor execution request."""

    adapter: Any
    workspace: Path
    task_bundle_path: Path
    jobs_dir: Path
    run_harbor_dir: Path
    run_id: str
    timeout_sec: int
    run_env: dict[str, str]


@dataclass(frozen=True, slots=True)
class TaskImageRef:
    """Content-addressed Docker image reference for Harbor execution."""

    image_name: str
    cache_key: str
    tag: str


@dataclass(frozen=True, slots=True)
class TaskImageBuildResult:
    """Result of a Harbor task image build."""

    completed_process: subprocess.CompletedProcess[str]
    timed_out: bool = False
    timeout_sec: int | None = None


@dataclass(frozen=True, slots=True)
class BaselineWorkspaceCacheResult:
    """Cache result for the shared prepared baseline workspace."""

    metadata_path: Path
    baseline_fingerprint: str
    hit: bool
    status: str


@dataclass(frozen=True, slots=True)
class WorkspacePreparationPhaseResult:
    """Workspace preparation phase output."""

    layout: RunLayout
    context: WorkspaceContext
    harbor_request: HarborExecutionRequest
    prep_phase_timings_sec: dict[str, float]
    prep_total_sec: float
    cache_metadata: dict[str, Any]
    auth_metadata: dict[str, Any]
    screenshot_command: tuple[str, ...] | None
    evidence_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionPhaseResult:
    """Harbor execution + verifier loading phase output."""

    harbor_result: HarborExecutionResult
    terminated_early: bool
    termination_reason: str | None
    process_metrics: ProcessMetrics
    events: list[TraceEvent]
    outputs: EvaluationOutputs
    duration_sec: float
    prep_phase_timings_sec: dict[str, float]
    prep_total_sec: float
    cache_metadata: dict[str, Any]
    auth_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PersistedArtifacts:
    """Persisted artifact metadata used for score synthesis."""

    starter_meta: dict
    scenario_revision_meta: dict[str, str | None]
    verifier_artifacts: dict[str, str]
    harness_artifacts: dict[str, str]
    harbor_artifacts: dict[str, str]
    evidence_artifacts: dict[str, Any]
    workspace_prune: dict[str, Any]
    workspace_changes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ScorecardBuildContext:
    """Input bundle for scorecard synthesis."""

    request: RunRequest
    layout: RunLayout
    context: WorkspaceContext
    artifacts: PersistedArtifacts
    execution: ExecutionPhaseResult
