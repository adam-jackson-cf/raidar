"""Typed in-process Raidar service boundary for autoresearch."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from raidar.application.execution import dispatch_from_experiment_request
from raidar.application.models import (
    ExperimentRunRequest,
    ScenarioCloneRequest,
    ScenarioInitRequest,
    ScenarioInitResult,
    ScenarioValidationResult,
    SuiteExecutionResult,
)
from raidar.application.scenarios import (
    clone_scenario_revision as clone_scenario_revision_service,
)
from raidar.application.scenarios import (
    init_scenario as init_scenario_service,
)
from raidar.application.scenarios import (
    validate_scenario as validate_scenario_service,
)
from raidar.scenario_clone import ScenarioCloneResult

from .storage import WorkspaceLayout


class RaidarClient(Protocol):
    """Evaluator boundary required by the autoresearch engine."""

    def scenario_init(self, request: ScenarioInitRequest) -> ScenarioInitResult: ...

    def scenario_clone_revision(self, request: ScenarioCloneRequest) -> ScenarioCloneResult: ...

    def scenario_validate(self, *, scenario_yaml: Path) -> ScenarioValidationResult: ...

    def experiment_run(self, request: ExperimentRunRequest) -> SuiteExecutionResult: ...


@dataclass(frozen=True, slots=True)
class RaidarServiceClient:
    """Call Raidar through its in-process typed service boundary."""

    layout: WorkspaceLayout

    def scenario_init(self, request: ScenarioInitRequest) -> ScenarioInitResult:
        return init_scenario_service(request)

    def scenario_clone_revision(self, request: ScenarioCloneRequest) -> ScenarioCloneResult:
        return clone_scenario_revision_service(request)

    def scenario_validate(self, *, scenario_yaml: Path) -> ScenarioValidationResult:
        return validate_scenario_service(scenario_yaml)

    def experiment_run(self, request: ExperimentRunRequest) -> SuiteExecutionResult:
        return dispatch_from_experiment_request(request, repo_root=self.layout.repo_root)
