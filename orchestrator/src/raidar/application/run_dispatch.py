"""Repeat execution and run persistence services."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime
from pathlib import Path

from click import ClickException

from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.application.models import RunCliOptions
from raidar.application.scenario_catalog import load_scenario
from raidar.findings import run_findings_artifact
from raidar.runtime.models import RunRequest
from raidar.runtime.pipeline import run_task
from raidar.runtime.starter_preflight import StarterPreflightError
from raidar.sanitization import sanitized_model_dump_json


def summary_result_path(run) -> Path:
    run_meta = run.scores.metadata.get("run", {})
    run_json_path = run_meta.get("run_json_path")
    if not isinstance(run_json_path, str):
        raise ClickException("Canonical run.json path missing from run metadata.")
    return Path(run_json_path)


def persist_eval_run(run) -> Path:
    result_path = summary_result_path(run)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(sanitized_model_dump_json(run, indent=2) + "\n", encoding="utf-8")
    persist_run_findings(run, result_path.parent)
    return result_path


def persist_run_findings(run, run_dir: Path) -> Path:
    findings_path = run_dir / "findings.json"
    findings_path.write_text(
        sanitized_model_dump_json(run_findings_artifact(run), indent=2) + "\n",
        encoding="utf-8",
    )
    return findings_path


def execute_repeat_runs(
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


def prepared_run_request(
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
    persist_eval_run(run)
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
