"""Typed request and result models for the Raidar application layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from raidar.schemas.scenario import ScenarioDefinition
from raidar.schemas.scorecard import EvalRun

ExperimentKind = Literal["benchmark", "research-loop"]


@dataclass(frozen=True, slots=True)
class RunCliOptionsBuildRequest:
    """Input for building normalized run CLI options."""

    scenario: Path
    harness: str
    provider: str
    model: str
    reasoning_effort: str | None
    timeout: int
    repeats: int
    repeat_parallel: int
    rerun_unscored: int
    experiments_root: Path | None
    experiment_kind: str | None
    repo_root: Path


@dataclass(frozen=True, slots=True)
class SuiteResultRequest:
    """Input for assembling a suite execution result."""

    resolved: RunCliOptions
    scenario: ScenarioDefinition
    runs: list[EvalRun]
    retries_used: int
    echo: bool
    experiment_json_path: Path | None = None
    summary_path: Path | None = None
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ExperimentSummaryPersistenceRequest:
    """Input for persisting experiment summary artifacts."""

    resolved: RunCliOptions
    scenario: ScenarioDefinition
    runs: list[EvalRun]
    started_at: datetime
    retries_used: int
    unresolved_unscored: int
    execution_dir: Path


@dataclass(frozen=True, slots=True)
class ExperimentDispatchSettings:
    """Execution settings for an experiment request dispatch."""

    repo_root: Path
    force_experiment_summary: bool = True
    cleanup_before_runs: bool = True
    echo: bool = False
    execution_suffix: str | None = None


@dataclass(frozen=True, slots=True)
class RunCliOptions:
    """Normalized CLI options for scenario execution commands."""

    scenario: Path
    harness: str
    provider: str
    model: str
    timeout: int
    repeats: int
    repeat_parallel: int
    rerun_unscored: int
    experiments_root: Path = Path(".")
    reasoning_effort: str | None = None

    def resolved(self) -> RunCliOptions:
        return RunCliOptions(
            scenario=self.scenario.resolve(),
            harness=self.harness,
            provider=self.provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            timeout=self.timeout,
            repeats=self.repeats,
            repeat_parallel=self.repeat_parallel,
            rerun_unscored=min(self.rerun_unscored, 1),
            experiments_root=self.experiments_root.resolve(),
        )


@dataclass(frozen=True, slots=True)
class ExecutionDispatchRequest:
    """Application-layer request for run and experiment commands."""

    options: RunCliOptions
    force_experiment_summary: bool
    cleanup_before_runs: bool
    echo: bool
    execution_suffix: str | None = None


@dataclass(frozen=True, slots=True)
class ExperimentRunRequest:
    """Typed request for repeated experiment execution."""

    scenario: Path
    harness: str
    provider: str
    model: str
    timeout: int
    repeats: int
    repeat_parallel: int
    rerun_unscored: int
    experiment_kind: ExperimentKind
    experiments_root: Path | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class SuiteExecutionResult:
    """Canonical experiment execution outcome for experiment and matrix flows."""

    scenario_path: Path
    scenario_name: str
    scenario_revision: str
    runs: list[EvalRun]
    retries_used: int
    experiment_json_path: Path | None = None
    summary_path: Path | None = None
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ScenarioInitRequest:
    """Typed request for scenario initialization."""

    path: Path
    name: str | None
    scenario_revision: str
    starter_root: str
    prompt_entry: str
    difficulty: Literal["easy", "medium", "hard"]
    category: str
    timeout_sec: int


@dataclass(frozen=True, slots=True)
class ScenarioInitResult:
    """Artifacts created during scenario initialization."""

    scenario_root: Path
    scenario_name: str
    scenario_revision: str
    parent_revision: str | None
    revision_dir: Path
    scenario_yaml: Path
    prompt_path: Path
    rules_dir: Path
    starter_root: str


@dataclass(frozen=True, slots=True)
class ScenarioCloneRequest:
    """Typed request for scenario-revision cloning."""

    path: Path
    from_revision: str
    to_revision: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioValidationResult:
    """Typed validation result for a scenario document."""

    scenario_path: Path
    scenario: ScenarioDefinition
