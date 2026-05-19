"""Execution-oriented application services for Raidar CLI and integrations."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime
from pathlib import Path

from click import ClickException
from dotenv import load_dotenv

from raidar.agents.config import AgentSpec, Harness, ModelTarget
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
from raidar.application.scenario_catalog import (
    load_scenario,
    scenario_evaluation_profile,
    scenario_metrics,
)
from raidar.experiment import (
    ExperimentSummaryInput,
    create_experiment_summary,
    persist_experiment,
)
from raidar.runner import RunRequest, StarterPreflightError
from raidar.runtime.maintenance import cleanup_stale_harbor_resources
from raidar.runtime.pipeline import run_task
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

    scenario_def, started_at, execution_dir, run_request = _prepared_run_request(
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


def _execute_dispatch_runs(
    resolved: RunCliOptions, run_request: RunRequest
) -> tuple[list, int, int]:
    return _execute_repeat_runs(
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


def _summary_result_path(run) -> Path:
    run_meta = run.scores.metadata.get("run", {})
    run_json_path = run_meta.get("run_json_path")
    if not isinstance(run_json_path, str):
        raise ClickException("Canonical run.json path missing from run metadata.")
    return Path(run_json_path)


def _persist_eval_run(run) -> Path:
    result_path = _summary_result_path(run)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return result_path


def _build_repeat_request(base_request: RunRequest, repeat_index: int) -> RunRequest:
    return RunRequest(
        scenario=base_request.scenario,
        config=base_request.config,
        scenario_dir=base_request.scenario_dir,
        execution_dir=base_request.execution_dir,
        repeat_index=repeat_index,
    )


def _execute_run_request(run_request: RunRequest):
    run = run_task(run_request)
    _persist_eval_run(run)
    return run


def _execute_repeat_index(request: RunRequest, repeat_index: int):
    try:
        return _execute_run_request(_build_repeat_request(request, repeat_index))
    except StarterPreflightError:
        raise
    except Exception as exc:
        raise ClickException(f"Repeat {repeat_index} failed: {exc}") from exc


def _execute_repeat_batch(
    *,
    request: RunRequest,
    batch_size: int,
    repeat_parallel: int,
    start_index: int,
) -> list:
    if batch_size <= 0:
        return []
    if repeat_parallel <= 1:
        return [
            _execute_repeat_index(request, start_index + offset) for offset in range(batch_size)
        ]

    resolved_parallel = max(1, min(repeat_parallel, batch_size))
    by_index: dict[int, object] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=resolved_parallel) as executor:
        future_map = {
            executor.submit(_execute_repeat_index, request, start_index + offset): offset
            for offset in range(batch_size)
        }
        for future in concurrent.futures.as_completed(future_map):
            by_index[future_map[future]] = future.result()
    return [by_index[idx] for idx in sorted(by_index)]


def _count_unscored(runs: list) -> int:
    return sum(1 for run in runs if run.scores.unscored)


def _run_with_unscored_reruns(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> tuple[list, int, int]:
    all_runs: list = []
    next_repeat_index = 1
    pending_batch = repeats
    retries_used = 0

    try:
        initial_runs = _execute_repeat_batch(
            request=request,
            batch_size=pending_batch,
            repeat_parallel=repeat_parallel,
            start_index=next_repeat_index,
        )
    except StarterPreflightError as exc:
        raise ClickException(
            f"Fatal starter preflight error. Experiment aborted without retries: {exc}"
        ) from exc
    all_runs.extend(initial_runs)
    pending_batch = _count_unscored(initial_runs)
    next_repeat_index += len(initial_runs)

    if pending_batch > 0 and rerun_unscored > 0:
        retries_used = 1
        try:
            retry_runs = _execute_repeat_batch(
                request=request,
                batch_size=pending_batch,
                repeat_parallel=repeat_parallel,
                start_index=next_repeat_index,
            )
        except StarterPreflightError as exc:
            raise ClickException(
                f"Fatal starter preflight error. Experiment aborted without retries: {exc}"
            ) from exc
        all_runs.extend(retry_runs)
        pending_batch = _count_unscored(retry_runs)

    return all_runs, retries_used, pending_batch


def _build_agent_spec(options: RunCliOptions) -> AgentSpec:
    return AgentSpec(
        harness=Harness(options.harness),
        model=ModelTarget(
            provider=options.provider,
            name=options.model,
            reasoning_effort=options.reasoning_effort,
        ),
        timeout_sec=options.timeout,
    )


def _execution_id(
    scenario_name: str,
    scenario_revision: str,
    started_at: datetime,
    execution_suffix: str | None = None,
) -> str:
    scenario_slug = scenario_name.lower().replace(" ", "-")
    base = f"{started_at.strftime('%Y%m%d-%H%M%SZ')}__{scenario_slug}__{scenario_revision}"
    if execution_suffix is None:
        return base
    return f"{base}__{execution_suffix}"


def _prepared_run_request(
    resolved: RunCliOptions,
    *,
    execution_suffix: str | None,
) -> tuple[object, datetime, Path, RunRequest]:
    scenario_def = load_scenario(resolved.scenario)
    started_at = datetime.now(UTC)
    execution_id = _execution_id(
        scenario_def.name,
        scenario_def.scenario_revision,
        started_at,
        execution_suffix=execution_suffix,
    )
    execution_dir = resolved.experiments_root / execution_id
    execution_dir.mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        scenario=scenario_def,
        config=_build_agent_spec(resolved),
        scenario_dir=resolved.scenario.parent,
        execution_dir=execution_dir,
        repeat_index=1,
    )
    return scenario_def, started_at, execution_dir, request


def _execute_repeat_runs(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> tuple[list, int, int]:
    try:
        return _run_with_unscored_reruns(
            request=request,
            repeats=max(1, repeats),
            repeat_parallel=repeat_parallel,
            rerun_unscored=rerun_unscored,
        )
    except Exception as exc:
        raise ClickException(str(exc)) from exc


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
    result_path = _summary_result_path(result)
    print(f"Result saved to {result_path}")
    print(f"Run ID: {result.id}")
    print(f"Duration: {result.duration_sec:.1f}s")
    print(f"Terminated early: {result.terminated_early}")
    print(f"Unscored result: {bool(result.scores.unscored)}")
    if result.scores.unscored:
        print(f"Unscored reasons: {list(result.scores.unscored_reasons)}")
    if result.termination_reason:
        print(f"Reason: {result.termination_reason}")


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
