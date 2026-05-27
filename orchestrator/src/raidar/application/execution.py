"""Execution-oriented application services for Raidar CLI and integrations."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from raidar.application.models import (
    ExecutionDispatchRequest,
    ExperimentDispatchSettings,
    ExperimentRunRequest,
    ExperimentSummaryPersistenceRequest,
    RunCliOptions,
    RunCliOptionsBuildRequest,
    SuiteExecutionResult,
    SuiteResultRequest,
)
from raidar.application.run_dispatch import (
    execute_repeat_runs,
    prepared_run_request,
    summary_result_path,
)
from raidar.application.scenario_catalog import (
    scenario_evaluation_profile,
    scenario_metrics,
    scenario_scorers,
)
from raidar.experiment import (
    ExperimentSummaryInput,
    create_experiment_summary,
    persist_experiment,
)
from raidar.runtime.maintenance import cleanup_stale_harbor_resources
from raidar.sanitization import sanitize_evidence_payload, sanitize_evidence_text
from raidar.schemas.scenario import ScenarioDefinition

BENCHMARK_EXPERIMENTS_ROOT_NAME = "benchmarks"
RESEARCH_LOOP_EXPERIMENTS_ROOT_NAME = "research_loops"


def resolve_experiments_root(
    *,
    experiments_root: Path | None,
    experiment_kind: str | None,
    repo_root: Path,
) -> Path:
    """Resolve the canonical experiment root for one command."""

    if experiments_root is not None:
        return experiments_root.resolve()
    root = repo_root / "experiments"
    if experiment_kind == "research-loop":
        return root / RESEARCH_LOOP_EXPERIMENTS_ROOT_NAME
    return root / BENCHMARK_EXPERIMENTS_ROOT_NAME


def experiment_execution_suffix(options: RunCliOptions) -> str:
    """Return the canonical experiment execution suffix."""

    suffix = f"{options.harness}__{options.provider}-{options.model}"
    if options.reasoning_effort:
        suffix = f"{suffix}__{options.reasoning_effort}"
    return suffix


def build_run_cli_options_from_request(request: RunCliOptionsBuildRequest) -> RunCliOptions:
    """Build canonical run options from command or matrix inputs."""

    return RunCliOptions(
        scenario=request.scenario,
        harness=request.harness,
        provider=request.provider,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        timeout=request.timeout,
        repeats=request.repeats,
        repeat_parallel=request.repeat_parallel,
        rerun_unscored=request.rerun_unscored,
        experiments_root=resolve_experiments_root(
            experiments_root=request.experiments_root,
            experiment_kind=request.experiment_kind,
            repo_root=request.repo_root,
        ),
    )


def execute_run_command(
    request: ExecutionDispatchRequest, *, repo_root: Path
) -> SuiteExecutionResult:
    """Execute one run or experiment command through the typed application layer."""

    _load_project_env(repo_root)
    resolved = request.options.resolved()
    if request.cleanup_before_runs:
        _cleanup_stale_harbor_before_runs()

    scenario_def, started_at, execution_dir, run_request = prepared_run_request(
        resolved,
        execution_suffix=request.execution_suffix,
    )
    if request.echo:
        _echo_run_header(resolved, run_request.scenario.name)
        print("Running scenario...")

    runs, retries_used, unresolved_unscored = _execute_dispatch_runs(resolved, run_request)
    if resolved.repeats == 1 and not request.force_experiment_summary:
        return _single_suite_result(
            _suite_result_request(request, scenario_def, runs, retries_used)
        )
    return _persisted_suite_result(
        _suite_result_request(request, scenario_def, runs, retries_used),
        ExperimentSummaryPersistenceRequest(
            resolved=resolved,
            scenario=scenario_def,
            runs=runs,
            started_at=started_at,
            retries_used=retries_used,
            unresolved_unscored=unresolved_unscored,
            execution_dir=execution_dir,
        ),
    )


def _execute_dispatch_runs(resolved: RunCliOptions, run_request) -> tuple[list, int, int]:
    return execute_repeat_runs(
        request=run_request,
        repeats=resolved.repeats,
        repeat_parallel=resolved.repeat_parallel,
        rerun_unscored=resolved.rerun_unscored,
    )


def _suite_result_request(
    request: ExecutionDispatchRequest,
    scenario: ScenarioDefinition,
    runs: list,
    retries_used: int,
) -> SuiteResultRequest:
    return SuiteResultRequest(
        resolved=request.options.resolved(),
        scenario=scenario,
        runs=runs,
        retries_used=retries_used,
        echo=request.echo,
    )


def _persisted_suite_result(
    request: SuiteResultRequest,
    summary_request: ExperimentSummaryPersistenceRequest,
) -> SuiteExecutionResult:
    experiment_json_path, summary_path, report_path = _persist_experiment_summary(summary_request)
    return _experiment_suite_result(
        SuiteResultRequest(
            resolved=request.resolved,
            scenario=request.scenario,
            runs=request.runs,
            retries_used=request.retries_used,
            echo=request.echo,
            experiment_json_path=experiment_json_path,
            summary_path=summary_path,
            report_path=report_path,
        )
    )


def _single_suite_result(request: SuiteResultRequest) -> SuiteExecutionResult:
    if request.echo:
        _echo_single_run_result(request.runs[0])
    return SuiteExecutionResult(
        scenario_path=request.resolved.scenario,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        runs=request.runs,
        retries_used=request.retries_used,
    )


def _experiment_suite_result(request: SuiteResultRequest) -> SuiteExecutionResult:
    if request.echo:
        _echo_experiment_result(request)
    return SuiteExecutionResult(
        scenario_path=request.resolved.scenario,
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        runs=request.runs,
        retries_used=request.retries_used,
        experiment_json_path=request.experiment_json_path,
        summary_path=request.summary_path,
        report_path=request.report_path,
    )


def dispatch_from_experiment_request(
    request: ExperimentRunRequest,
    settings: ExperimentDispatchSettings,
) -> SuiteExecutionResult:
    """Convert an experiment request into the canonical dispatch request."""

    options = build_run_cli_options_from_request(
        RunCliOptionsBuildRequest(
            scenario=request.scenario,
            harness=request.harness,
            provider=request.provider,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            timeout=request.timeout,
            repeats=request.repeats,
            repeat_parallel=request.repeat_parallel,
            rerun_unscored=request.rerun_unscored,
            experiments_root=request.experiments_root,
            experiment_kind=request.experiment_kind,
            repo_root=settings.repo_root,
        )
    )
    force_summary = settings.force_experiment_summary
    resolved_suffix = settings.execution_suffix
    if resolved_suffix is None and force_summary:
        resolved_suffix = experiment_execution_suffix(options)
    return execute_run_command(
        ExecutionDispatchRequest(
            options=options,
            force_experiment_summary=force_summary,
            cleanup_before_runs=settings.cleanup_before_runs,
            echo=settings.echo,
            execution_suffix=resolved_suffix,
        ),
        repo_root=settings.repo_root,
    )


def _persist_experiment_summary(
    request: ExperimentSummaryPersistenceRequest,
) -> tuple[Path, Path, Path]:
    summary = create_experiment_summary(
        ExperimentSummaryInput(
            scenario_name=request.scenario.name,
            scenario_revision=request.scenario.scenario_revision,
            harness=request.resolved.harness,
            model=request.resolved.model,
            evaluation_profile=scenario_evaluation_profile(request.scenario),
            metrics=scenario_metrics(request.scenario),
            scorers=list(scenario_scorers(request.scenario)),
            repeats=request.resolved.repeats,
            repeat_parallel=max(1, min(request.resolved.repeat_parallel, request.resolved.repeats)),
            runs=request.runs,
            started_at=request.started_at,
            rerun_unscored_limit=request.resolved.rerun_unscored,
            reruns_used=request.retries_used,
            unresolved_unscored_count=request.unresolved_unscored,
        )
    )
    return persist_experiment(request.execution_dir, summary)


def _load_project_env(repo_root: Path) -> None:
    """Load the orchestrator environment file when present."""

    env_path = repo_root / "orchestrator" / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _cleanup_stale_harbor_before_runs() -> None:
    cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )


def _echo_run_header(options: RunCliOptions, scenario_name: str) -> None:
    print(f"Loading scenario from {options.scenario}")
    print(f"Scenario: {scenario_name}")
    print(f"Harness: {options.harness}")
    print(f"Model: {options.model}")
    print(f"Repeats: {options.repeats}")
    print(f"Repeat parallelism: {options.repeat_parallel}")
    print(f"Rerun unscored budget: {options.rerun_unscored}")


def _echo_single_run_result(result) -> None:
    run_meta = result.scores.metadata.get("run", {})
    canonical_dir = run_meta.get("canonical_run_dir")
    if isinstance(canonical_dir, str):
        print(f"Canonical run dir: {canonical_dir}")
    result_path = summary_result_path(result)
    print(f"Result saved to {result_path}")
    print(f"Run ID: {result.id}")
    print(f"Duration: {result.duration_sec:.1f}s")
    print(f"Terminated early: {result.terminated_early}")
    print(f"Unscored result: {bool(result.scores.unscored)}")
    if result.scores.unscored:
        print(
            f"Unscored reasons: {sanitize_evidence_payload(list(result.scores.unscored_reasons))}"
        )
    if result.termination_reason:
        print(f"Reason: {sanitize_evidence_text(result.termination_reason)}")


def _echo_experiment_result(request: SuiteResultRequest) -> None:
    print(f"Experiment record: {request.experiment_json_path}")
    print(f"Experiment summary: {request.summary_path}")
    print(f"Experiment report: {request.report_path}")
    print(f"Unscored retries used: {request.retries_used}")
    for run in request.runs:
        print(
            f"Run {run.id}: unscored={bool(run.scores.unscored)}, "
            f"execution_valid={run.scores.execution_validity.passed}, "
            f"performance_gates={run.scores.performance_gates.passed}, "
            f"composite={run.scores.composite_score:.3f}, duration={run.duration_sec:.1f}s"
        )
