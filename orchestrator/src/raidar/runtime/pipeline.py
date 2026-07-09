"""Top-level runtime phase pipeline for one evaluation run."""

from __future__ import annotations

import time
from dataclasses import is_dataclass, replace

from raidar.runtime.artifact_phase import persist_artifacts_phase
from raidar.runtime.execution_phase import execute_harbor_phase
from raidar.runtime.scorecard_phase import synthesize_scorecard_phase
from raidar.runtime.workspace import scenario_evaluation_profile, scenario_scorers
from raidar.runtime.workspace_phase import prepare_workspace_phase
from raidar.schemas.scorecard import EvalConfig, EvalRun


def run_task(request):
    """Execute a scenario and return evaluation results."""

    run_started_at = time.perf_counter()
    prepared = prepare_workspace_phase(request)
    time_to_experiment_start_sec = round(time.perf_counter() - run_started_at, 3)
    if is_dataclass(prepared):
        prepared = replace(
            prepared,
            time_to_experiment_start_sec=time_to_experiment_start_sec,
        )
    else:
        prepared.time_to_experiment_start_sec = time_to_experiment_start_sec
    execution = execute_harbor_phase(request, prepared)
    artifacts = persist_artifacts_phase(request, prepared, execution)
    scorecard = synthesize_scorecard_phase(request, prepared, execution, artifacts)
    return EvalRun(
        id=prepared.layout.run_id,
        timestamp=prepared.layout.start_time.isoformat(),
        config=EvalConfig(
            model=request.config.model.qualified_name,
            harness=request.config.harness.value,
            scenario_name=request.scenario.name,
            scenario_revision=request.scenario.scenario_revision,
            starter_root=request.scenario.starter.root,
            evaluation_profile=scenario_evaluation_profile(request.scenario),
            scorers=scenario_scorers(request.scenario),
        ),
        duration_sec=execution.duration_sec,
        terminated_early=execution.terminated_early,
        termination_reason=execution.termination_reason,
        scores=scorecard,
        traces=execution.events,
        gate_history=execution.outputs.gate_history,
    )
