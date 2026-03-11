"""CLI entrypoint for the scenario/experiment orchestrator."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
import yaml
from dotenv import load_dotenv

from .agents.adapters.registry import registry
from .agents.config import AgentSpec, Harness, ModelTarget
from .agents.rules import SYSTEM_RULES, inject_rules

if TYPE_CHECKING:
    from .runner import RunRequest
    from .schemas.scenario import ScenarioDefinition
    from .schemas.scorecard import EvalRun

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ORCHESTRATOR_ROOT / ".env"
ARTIFACT_CHANGE_PREFIXES = ("experiments/",)
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"
DEFAULT_ARCHIVE_ROOT = Path("/tmp")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(package_name="raidar")
def main() -> None:
    """Scenario/experiment orchestrator for harness/model evaluation runs."""


HARNESS_CHOICES = [harness.value for harness in Harness]
VERSION_DIR_PATTERN = re.compile(r"^v(\d+)$")
INTEGRATION_TEST_TARGET = "tests/test_runner_harbor_env_and_cleanup.py"
TYPECHECK_TARGETS = [
    "src/raidar/watcher",
    "src/raidar/agents/adapters",
    "tests/test_codex_cli_adapter.py",
    "tests/test_claude_code_cli_adapter.py",
    "tests/test_gemini_cli_adapter.py",
]
COVERAGE_FAIL_UNDER = "60"


@dataclass(frozen=True, slots=True)
class RunCliOptions:
    """Normalized CLI options for scenario execution commands."""

    scenario: Path
    harness: str
    model: str
    timeout: int
    repeats: int
    repeat_parallel: int
    rerun_unscored: int

    def resolved(self) -> RunCliOptions:
        return RunCliOptions(
            scenario=self.scenario.resolve(),
            harness=self.harness,
            model=self.model,
            timeout=self.timeout,
            repeats=self.repeats,
            repeat_parallel=self.repeat_parallel,
            rerun_unscored=min(self.rerun_unscored, 1),
        )


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


def _runner_api() -> Any:
    from . import runner as runner_module

    return runner_module


def _experiment_api() -> Any:
    from . import experiment as experiment_module

    return experiment_module


def _scenario_clone_api() -> Any:
    from . import scenario_clone as scenario_clone_module

    return scenario_clone_module


def _cleanup_stale_harbor_before_runs() -> None:
    _runner_api().cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )


def _summary_result_path(run: EvalRun) -> Path:
    run_meta = run.scores.metadata.get("run", {})
    run_json_path = run_meta.get("run_json_path")
    if not isinstance(run_json_path, str):
        raise click.ClickException("Canonical run.json path missing from run metadata.")
    return Path(run_json_path)


def _persist_eval_run(run: EvalRun) -> Path:
    result_path = _summary_result_path(run)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(run.model_dump_json(indent=2))
    return result_path


def _experiment_execution_suffix(options: RunCliOptions) -> str:
    return f"{options.harness}__{options.model.replace('/', '-')}"


def _build_repeat_request(base_request: RunRequest, repeat_index: int) -> RunRequest:
    run_request = _runner_api().RunRequest
    return run_request(
        scenario=base_request.scenario,
        config=base_request.config,
        scenario_dir=base_request.scenario_dir,
        execution_dir=base_request.execution_dir,
        repeat_index=repeat_index,
    )


def _execute_run_request(run_request: RunRequest) -> EvalRun:
    run = _runner_api().run_task(run_request)
    _persist_eval_run(run)
    return run


def _execute_repeat_index(request: RunRequest, repeat_index: int) -> EvalRun:
    try:
        return _execute_run_request(_build_repeat_request(request, repeat_index))
    except _runner_api().StarterPreflightError:
        raise
    except Exception as exc:
        raise click.ClickException(f"Repeat {repeat_index} failed: {exc}") from exc


def _execute_repeat_batch_sequential(
    *,
    request: RunRequest,
    batch_size: int,
    start_index: int,
) -> list[EvalRun]:
    runs: list[EvalRun] = []
    for offset in range(batch_size):
        runs.append(_execute_repeat_index(request, start_index + offset))
    return runs


def _execute_repeat_batch_parallel(
    *,
    request: RunRequest,
    batch_size: int,
    repeat_parallel: int,
    start_index: int,
) -> list[EvalRun]:
    resolved_parallel = max(1, min(repeat_parallel, batch_size))
    by_index: dict[int, EvalRun] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=resolved_parallel) as executor:
        future_map = {
            executor.submit(_execute_repeat_index, request, start_index + offset): offset
            for offset in range(batch_size)
        }
        for future in concurrent.futures.as_completed(future_map):
            offset = future_map[future]
            by_index[offset] = future.result()
    return [by_index[idx] for idx in sorted(by_index)]


def _execute_repeat_batch(
    *,
    request: RunRequest,
    batch_size: int,
    repeat_parallel: int,
    start_index: int,
) -> list[EvalRun]:
    if batch_size <= 0:
        return []
    if repeat_parallel <= 1:
        return _execute_repeat_batch_sequential(
            request=request,
            batch_size=batch_size,
            start_index=start_index,
        )
    return _execute_repeat_batch_parallel(
        request=request,
        batch_size=batch_size,
        repeat_parallel=repeat_parallel,
        start_index=start_index,
    )


def _run_is_unscored(run: EvalRun) -> bool:
    return bool(run.scores.unscored)


def _run_unscored_reasons(run: EvalRun) -> list[str]:
    return list(run.scores.unscored_reasons)


def _count_unscored(runs: list[EvalRun]) -> int:
    return sum(1 for run in runs if _run_is_unscored(run))


def _run_with_unscored_reruns(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> tuple[list[EvalRun], int, int]:
    all_runs: list[EvalRun] = []
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
    except _runner_api().StarterPreflightError as exc:
        raise click.ClickException(
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
        except _runner_api().StarterPreflightError as exc:
            raise click.ClickException(
                f"Fatal starter preflight error. Experiment aborted without retries: {exc}"
            ) from exc
        all_runs.extend(retry_runs)
        pending_batch = _count_unscored(retry_runs)

    return all_runs, retries_used, pending_batch


def _build_agent_spec(options: RunCliOptions) -> AgentSpec:
    return AgentSpec(
        harness=Harness(options.harness),
        model=ModelTarget.from_string(options.model),
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
    if not execution_suffix:
        return base
    return f"{base}__{execution_suffix}"


def _build_run_request(
    options: RunCliOptions, scenario_def: ScenarioDefinition, execution_dir: Path
) -> RunRequest:
    config = _build_agent_spec(options)
    execution_dir.mkdir(parents=True, exist_ok=True)
    return _runner_api().RunRequest(
        scenario=scenario_def,
        config=config,
        scenario_dir=options.scenario.parent,
        execution_dir=execution_dir,
        repeat_index=1,
    )


def _echo_run_header(options: RunCliOptions, scenario_name: str) -> None:
    click.echo(f"Loading scenario from {options.scenario}")
    click.echo(f"Scenario: {scenario_name}")
    click.echo(f"Harness: {options.harness}")
    click.echo(f"Model: {options.model}")
    click.echo(f"Repeats: {options.repeats}")
    click.echo(f"Repeat parallelism: {options.repeat_parallel}")
    click.echo(f"Rerun unscored budget: {options.rerun_unscored}")


def _echo_single_run_result(result: EvalRun) -> None:
    run_meta = result.scores.metadata.get("run", {})
    canonical_dir = run_meta.get("canonical_run_dir")
    if isinstance(canonical_dir, str):
        click.echo(f"Canonical run dir: {canonical_dir}")
    result_path = _summary_result_path(result)
    click.echo(f"Result saved to {result_path}")
    click.echo(f"Run ID: {result.id}")
    click.echo(f"Duration: {result.duration_sec:.1f}s")
    click.echo(f"Terminated early: {result.terminated_early}")
    click.echo(f"Unscored result: {_run_is_unscored(result)}")
    if _run_is_unscored(result):
        click.echo(f"Unscored reasons: {_run_unscored_reasons(result)}")
    if result.termination_reason:
        click.echo(f"Reason: {result.termination_reason}")


def _echo_experiment_result(
    experiment_json_path: Path,
    summary_path: Path,
    report_path: Path,
    retries_used: int,
    runs: list[EvalRun],
) -> None:
    click.echo(f"Experiment record: {experiment_json_path}")
    click.echo(f"Experiment summary: {summary_path}")
    click.echo(f"Experiment report: {report_path}")
    click.echo(f"Unscored retries used: {retries_used}")
    for run in runs:
        click.echo(
            f"Run {run.id}: unscored={_run_is_unscored(run)}, "
            f"execution_valid={run.scores.execution_validity.passed}, "
            f"performance_gates={run.scores.performance_gates.passed}, "
            f"composite={run.scores.composite_score:.3f}, duration={run.duration_sec:.1f}s"
        )


def _prepared_run_request(
    resolved: RunCliOptions,
    *,
    execution_suffix: str | None,
) -> tuple[ScenarioDefinition, datetime, Path, RunRequest]:
    scenario_def = _runner_api().load_scenario(resolved.scenario)
    started_at = datetime.now(UTC)
    execution_id = _execution_id(
        scenario_def.name,
        scenario_def.scenario_revision,
        started_at,
        execution_suffix=execution_suffix,
    )
    execution_dir = EXPERIMENTS_ROOT / execution_id
    request = _build_run_request(resolved, scenario_def, execution_dir)
    return scenario_def, started_at, execution_dir, request


def _execute_repeat_runs(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> tuple[list[EvalRun], int, int]:
    try:
        return _run_with_unscored_reruns(
            request=request,
            repeats=max(1, repeats),
            repeat_parallel=repeat_parallel,
            rerun_unscored=rerun_unscored,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


def _single_run_execution_result(
    *,
    resolved: RunCliOptions,
    scenario_def: ScenarioDefinition,
    runs: list[EvalRun],
    retries_used: int,
    echo: bool,
) -> SuiteExecutionResult:
    if echo:
        _echo_single_run_result(runs[0])
    return SuiteExecutionResult(
        scenario_path=resolved.scenario,
        scenario_name=scenario_def.name,
        scenario_revision=scenario_def.scenario_revision,
        runs=runs,
        retries_used=retries_used,
    )


def _persist_experiment_execution(
    *,
    resolved: RunCliOptions,
    request: RunRequest,
    scenario_def: ScenarioDefinition,
    execution_dir: Path,
    started_at: datetime,
    runs: list[EvalRun],
    retries_used: int,
    unresolved_unscored: int,
    echo: bool,
) -> SuiteExecutionResult:
    runner_api = _runner_api()
    experiment_api = _experiment_api()
    experiment_summary = experiment_api.create_experiment_summary(
        scenario_name=request.scenario.name,
        scenario_revision=request.scenario.scenario_revision,
        harness=resolved.harness,
        model=resolved.model,
        evaluation_profile=runner_api.scenario_evaluation_profile(request.scenario),
        metrics=runner_api.scenario_metrics(request.scenario),
        repeats=resolved.repeats,
        repeat_parallel=max(1, min(resolved.repeat_parallel, resolved.repeats)),
        runs=runs,
        started_at=started_at,
        rerun_unscored_limit=resolved.rerun_unscored,
        reruns_used=retries_used,
        unresolved_unscored_count=unresolved_unscored,
    )
    experiment_json_path, summary_path, report_path = experiment_api.persist_experiment(
        execution_dir,
        experiment_summary,
    )
    if echo:
        _echo_experiment_result(experiment_json_path, summary_path, report_path, retries_used, runs)
    return SuiteExecutionResult(
        scenario_path=resolved.scenario,
        scenario_name=scenario_def.name,
        scenario_revision=scenario_def.scenario_revision,
        runs=runs,
        retries_used=retries_used,
        experiment_json_path=experiment_json_path,
        summary_path=summary_path,
        report_path=report_path,
    )


def _execute_run_options(
    options: RunCliOptions,
    *,
    force_experiment_summary: bool,
    cleanup_before_runs: bool,
    echo: bool,
    execution_suffix: str | None = None,
) -> SuiteExecutionResult:
    resolved = options.resolved()
    if cleanup_before_runs:
        _cleanup_stale_harbor_before_runs()

    scenario_def, started_at, execution_dir, request = _prepared_run_request(
        resolved,
        execution_suffix=execution_suffix,
    )
    if echo:
        _echo_run_header(resolved, request.scenario.name)
        click.echo("Running scenario...")

    runs, retries_used, unresolved_unscored = _execute_repeat_runs(
        request=request,
        repeats=resolved.repeats,
        repeat_parallel=resolved.repeat_parallel,
        rerun_unscored=resolved.rerun_unscored,
    )

    if resolved.repeats == 1 and not force_experiment_summary:
        return _single_run_execution_result(
            resolved=resolved,
            scenario_def=scenario_def,
            runs=runs,
            retries_used=retries_used,
            echo=echo,
        )

    return _persist_experiment_execution(
        resolved=resolved,
        request=request,
        scenario_def=scenario_def,
        execution_dir=execution_dir,
        started_at=started_at,
        runs=runs,
        retries_used=retries_used,
        unresolved_unscored=unresolved_unscored,
        echo=echo,
    )


def _repo_paths_from_git_cmd(args: list[str]) -> list[str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(result.stderr.strip() or f"Command failed: {' '.join(args)}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repo_name_status_from_git_cmd(args: list[str]) -> list[tuple[str, str]]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(result.stderr.strip() or f"Command failed: {' '.join(args)}")
    entries: list[tuple[str, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        entries.append((status, path))
    return entries


def _changed_repo_paths(repo_root: Path) -> list[str]:
    staged = _repo_paths_from_git_cmd(
        ["git", "-C", str(repo_root), "diff", "--name-only", "--cached"]
    )
    unstaged = _repo_paths_from_git_cmd(["git", "-C", str(repo_root), "diff", "--name-only"])
    untracked = _repo_paths_from_git_cmd(
        ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"]
    )
    return sorted(set(staged + unstaged + untracked))


def _generated_artifact_paths(paths: list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if any(path.startswith(prefix) for prefix in ARTIFACT_CHANGE_PREFIXES)
    )


def _changed_repo_entries(repo_root: Path) -> list[tuple[str, str]]:
    staged = _repo_name_status_from_git_cmd(
        ["git", "-C", str(repo_root), "diff", "--name-status", "--cached"]
    )
    unstaged = _repo_name_status_from_git_cmd(
        ["git", "-C", str(repo_root), "diff", "--name-status"]
    )
    untracked = [
        (("??"), path)
        for path in _repo_paths_from_git_cmd(
            ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"]
        )
    ]
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for entry in staged + unstaged + untracked:
        if entry in seen:
            continue
        seen.add(entry)
        deduped.append(entry)
    return deduped


def _assert_no_generated_artifact_changes(repo_root: Path) -> None:
    changed_entries = _changed_repo_entries(repo_root)
    matches = [
        path
        for status, path in changed_entries
        if not status.startswith("D")
        and any(path.startswith(prefix) for prefix in ARTIFACT_CHANGE_PREFIXES)
    ]
    if not matches:
        return
    listed = "\n".join(f"- {path}" for path in matches)
    raise click.ClickException(
        "Generated Harbor artifacts must not be committed. Remove these changes:\n" + listed
    )


def _has_unstaged_changes(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0


def _run_or_raise(cmd: list[str], cwd: Path) -> None:
    rendered = " ".join(cmd)
    click.echo(f"[exec] {rendered}")
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        raise click.ClickException(f"Command failed ({result.returncode}): {rendered}")


def _load_json_file(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _load_scenario_document(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise click.ClickException(f"Scenario document must be a mapping: {path}")
    return payload


def _write_scenario_document(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def _scenario_revision_sort_key(scenario_yaml: Path) -> tuple[int, str]:
    revision_dir = scenario_yaml.parent.name
    match = VERSION_DIR_PATTERN.fullmatch(revision_dir)
    if match is None:
        return (-1, revision_dir)
    return (int(match.group(1)), revision_dir)


def _resolve_scenario_yaml(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_file():
        return resolved
    scenario_yaml = resolved / "scenario.yaml"
    if scenario_yaml.is_file():
        return scenario_yaml
    candidates = list(resolved.glob("v*/scenario.yaml"))
    if not candidates:
        raise click.ClickException(f"scenario.yaml not found in {resolved}")
    return max(candidates, key=_scenario_revision_sort_key)


def _execution_payload(execution_dir: Path) -> dict[str, object]:
    for candidate in (
        execution_dir / "experiment-summary.json",
        execution_dir / "experiment.json",
        execution_dir / "runs" / "run-01" / "run.json",
    ):
        payload = _load_json_file(candidate)
        if payload is not None:
            return payload
    return {}


def _execution_name_parts(execution_id: str) -> tuple[str | None, str | None, str | None]:
    parts = execution_id.split("__")
    if len(parts) < 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


def _execution_record(execution_dir: Path) -> dict[str, object]:
    payload = _execution_payload(execution_dir)
    config = payload.get("config")
    aggregate = payload.get("aggregate")
    config_dict = config if isinstance(config, dict) else {}
    aggregate_dict = aggregate if isinstance(aggregate, dict) else {}
    _, scenario_from_name, revision_from_name = _execution_name_parts(execution_dir.name)
    scenario_name = str(
        config_dict.get("scenario_name") or scenario_from_name or "unknown-scenario"
    )
    scenario_revision = str(
        config_dict.get("scenario_revision") or revision_from_name or "unknown-revision"
    )
    return {
        "execution_id": execution_dir.name,
        "path": str(execution_dir),
        "created_at_utc": payload.get("created_at_utc"),
        "scenario_name": scenario_name,
        "scenario_revision": scenario_revision,
        "harness": config_dict.get("harness"),
        "model": config_dict.get("model"),
        "evaluation_profile": config_dict.get("evaluation_profile"),
        "metrics": config_dict.get("metrics"),
        "run_count_total": aggregate_dict.get("run_count_total"),
        "unscored_count": aggregate_dict.get("unscored_count"),
    }


def _execution_model_key(execution_dir: Path) -> str:
    payload = _execution_payload(execution_dir)
    config = payload.get("config")
    config_dict = config if isinstance(config, dict) else {}
    model = config_dict.get("model")
    if isinstance(model, str) and model:
        return model.replace("/", "__")
    return "unknown-model"


def _archive_destination(src: Path, archive_dir: Path) -> Path:
    try:
        rel = src.relative_to(REPO_ROOT)
    except ValueError:
        rel = Path("experiments") / src.name
    return archive_dir / rel


def _archive_path(src: Path, archive_dir: Path, *, dry_run: bool) -> bool:
    if not src.exists():
        return False
    destination = _archive_destination(src, archive_dir)
    rel = destination.relative_to(archive_dir)
    if dry_run:
        click.echo(f"would-archive: {rel}")
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(destination))
    click.echo(f"archived: {rel}")
    return True


def _sorted_experiment_dirs(experiments_root: Path) -> list[Path]:
    if not experiments_root.is_dir():
        return []
    return sorted(
        (path for path in experiments_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )


def _default_archive_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_ARCHIVE_ROOT / "raidar-archive" / stamp


def _execution_matches_filters(
    record: dict[str, object],
    *,
    scenario: str | None,
    model: str | None,
    harness: str | None,
    evaluation_profile: str | None,
) -> bool:
    scenario_value = str(record.get("scenario_name", "")).lower()
    model_value = str(record.get("model", "")).lower()
    harness_value = str(record.get("harness", "")).lower()
    evaluation_profile_value = str(record.get("evaluation_profile", "")).lower()
    if scenario and scenario.lower() not in scenario_value:
        return False
    if model and model.lower() not in model_value:
        return False
    if harness and harness.lower() not in harness_value:
        return False
    return not (evaluation_profile and evaluation_profile.lower() not in evaluation_profile_value)


@main.command()
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scenario.yaml file",
)
@click.option(
    "--harness",
    "-a",
    type=click.Choice(HARNESS_CHOICES),
    required=True,
    help="Harness to use",
)
@click.option(
    "--model",
    "-m",
    type=str,
    required=True,
    help="Model in format provider/name (e.g., openai/gpt-5)",
)
@click.option(
    "--timeout",
    type=int,
    default=1800,
    help="Scenario timeout in seconds",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=1,
    help="Number of repeated runs for the same configuration (use for smoke/debug loops)",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    help="Parallel workers for repeat runs",
)
@click.option(
    "--rerun-unscored",
    type=click.IntRange(min=0, max=1),
    default=0,
    help="Rerun budget for unscored runs (0 or 1; at most one rerun per failure)",
)
def run(
    scenario: Path,
    harness: str,
    model: str,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> None:
    """Run one scenario with the specified harness and model for smoke/debug workflows."""
    options = RunCliOptions(
        scenario=scenario,
        harness=harness,
        model=model,
        timeout=timeout,
        repeats=repeats,
        repeat_parallel=repeat_parallel,
        rerun_unscored=rerun_unscored,
    )
    _execute_run_options(
        options,
        force_experiment_summary=False,
        cleanup_before_runs=True,
        echo=True,
    )


@main.group()
def experiment() -> None:
    """Experiment-level run workflows."""


@experiment.command("run")
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scenario.yaml file",
)
@click.option(
    "--harness",
    "-a",
    type=click.Choice(HARNESS_CHOICES),
    required=True,
    help="Harness to use",
)
@click.option(
    "--model",
    "-m",
    type=str,
    required=True,
    help="Model in format provider/name",
)
@click.option(
    "--timeout",
    type=int,
    default=300,
    help="Scenario timeout in seconds",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=5,
    help="Number of repeated runs in the experiment",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    help="Parallel workers for repeat runs",
)
@click.option(
    "--rerun-unscored",
    type=click.IntRange(min=0, max=1),
    default=1,
    help="Rerun budget for unscored runs (0 or 1)",
)
def experiment_run(
    scenario: Path,
    harness: str,
    model: str,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
) -> None:
    """Run a repeated experiment with deterministic aggregate output."""
    options = RunCliOptions(
        scenario=scenario,
        harness=harness,
        model=model,
        timeout=timeout,
        repeats=repeats,
        repeat_parallel=repeat_parallel,
        rerun_unscored=rerun_unscored,
    )
    _execute_run_options(
        options,
        force_experiment_summary=True,
        cleanup_before_runs=True,
        echo=True,
        execution_suffix=_experiment_execution_suffix(options),
    )


@main.group()
def quality() -> None:
    """Quality gate commands."""


@quality.command("gates")
@click.option("--fix", is_flag=True, help="Apply auto-fixes where supported.")
@click.option("--stage", is_flag=True, help="Stage tracked file updates after fixes.")
def quality_gates(fix: bool, stage: bool) -> None:
    """Run deterministic quality gates for orchestrator source."""
    if stage and not fix:
        raise click.ClickException("--stage is only supported together with --fix.")
    if fix and _has_unstaged_changes(REPO_ROOT):
        raise click.ClickException(
            "Unstaged changes detected. Stage or stash before running --fix."
        )

    _assert_no_generated_artifact_changes(REPO_ROOT)

    if shutil.which("lizard") is None:
        raise click.ClickException("Missing required command: lizard")

    if fix:
        _run_or_raise(
            [sys.executable, "-m", "ruff", "format", "--force-exclude"], ORCHESTRATOR_ROOT
        )
        _run_or_raise(
            [sys.executable, "-m", "ruff", "check", ".", "--fix", "--force-exclude"],
            ORCHESTRATOR_ROOT,
        )
    else:
        _run_or_raise(
            [sys.executable, "-m", "ruff", "format", "--check", "--force-exclude"],
            ORCHESTRATOR_ROOT,
        )
        _run_or_raise(
            [sys.executable, "-m", "ruff", "check", ".", "--no-fix", "--force-exclude"],
            ORCHESTRATOR_ROOT,
        )

    _run_or_raise(["lizard", "-C", "10", "-l", "python", "src"], ORCHESTRATOR_ROOT)
    _run_or_raise(
        [sys.executable, "-m", "mypy", "--follow-imports=skip", *TYPECHECK_TARGETS],
        ORCHESTRATOR_ROOT,
    )
    _run_or_raise([sys.executable, "-m", "pytest", "tests", "-x", "--tb=short"], ORCHESTRATOR_ROOT)
    _run_or_raise(
        [sys.executable, "-m", "pytest", INTEGRATION_TEST_TARGET, "-x", "--tb=short"],
        ORCHESTRATOR_ROOT,
    )
    _run_or_raise(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "--cov=src",
            "--cov-report=term-missing:skip-covered",
            f"--cov-fail-under={COVERAGE_FAIL_UNDER}",
            "-x",
            "--tb=short",
        ],
        ORCHESTRATOR_ROOT,
    )

    if stage:
        _run_or_raise(["git", "-C", str(REPO_ROOT), "add", "-u"], REPO_ROOT)

    click.echo("[quality-gates] Completed successfully")


@main.group()
def harbor() -> None:
    """Harbor operational commands."""


@harbor.command("cleanup")
@click.option(
    "--include-containers/--no-include-containers",
    default=True,
    help="Remove stale stopped Harbor containers.",
)
@click.option(
    "--include-build-processes/--no-include-build-processes",
    default=True,
    help="Terminate stale orphan build processes.",
)
def harbor_cleanup(include_containers: bool, include_build_processes: bool) -> None:
    """Cleanup stale Harbor processes and containers."""
    _runner_api().cleanup_stale_harbor_resources(
        include_containers=include_containers,
        include_build_processes=include_build_processes,
    )
    click.echo("Harbor cleanup completed.")


@main.group()
def env() -> None:
    """Environment setup and diagnostics."""


@env.command("setup")
@click.option(
    "--install-tools/--no-install-tools",
    default=True,
    help="Install required toolchain components with uv.",
)
@click.option(
    "--sync-arg",
    multiple=True,
    help="Additional argument to pass to `uv sync`.",
)
def env_setup(install_tools: bool, sync_arg: tuple[str, ...]) -> None:
    """Setup local toolchain and run Harbor preflight checks."""
    _cleanup_stale_harbor_before_runs()

    reason = _runner_api()._docker_compose_preflight_reason(dict(os.environ))
    if reason:
        raise click.ClickException(reason)

    if install_tools:
        _run_or_raise(["uv", "python", "install", "3.12"], ORCHESTRATOR_ROOT)
        _run_or_raise(["uv", "sync", *sync_arg], ORCHESTRATOR_ROOT)
        _run_or_raise(["uv", "tool", "install", "harbor"], ORCHESTRATOR_ROOT)

    result = subprocess.run(["harbor", "--version"], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        click.echo(result.stdout.strip())
    click.echo("Environment setup completed.")


@main.group()
def experiments() -> None:
    """Experiment artifact workflows."""


@experiments.command("list")
@click.option(
    "--experiments-root",
    type=click.Path(path_type=Path),
    default=EXPERIMENTS_ROOT,
    show_default=True,
    help="Experiment directory root.",
)
@click.option("--scenario", type=str, help="Filter by scenario name substring.")
@click.option("--model", type=str, help="Filter by model substring.")
@click.option("--harness", type=str, help="Filter by harness substring.")
@click.option(
    "--evaluation-profile",
    type=str,
    help="Filter by evaluation profile substring.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Maximum rows to display.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def experiments_list(
    experiments_root: Path,
    scenario: str | None,
    model: str | None,
    harness: str | None,
    evaluation_profile: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """List experiments with optional filters."""
    dirs = _sorted_experiment_dirs(experiments_root.resolve())
    rows: list[dict[str, object]] = []
    for path in dirs:
        record = _execution_record(path)
        if not _execution_matches_filters(
            record,
            scenario=scenario,
            model=model,
            harness=harness,
            evaluation_profile=evaluation_profile,
        ):
            continue
        rows.append(record)
        if len(rows) >= limit:
            break

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No experiments found.")
        return

    for index, row in enumerate(rows, start=1):
        click.echo(
            f"{index:02d}. {row['execution_id']} | "
            f"scenario={row['scenario_name']}@{row['scenario_revision']} | "
            f"harness={row.get('harness') or 'unknown'} | "
            f"model={row.get('model') or 'unknown'} | "
            f"evaluation_profile={row.get('evaluation_profile') or 'unknown'} | "
            f"runs={row.get('run_count_total') or 0} | unscored={row.get('unscored_count') or 0}"
        )


@experiments.command("prune")
@click.option(
    "--experiments-root",
    type=click.Path(path_type=Path),
    default=EXPERIMENTS_ROOT,
    show_default=True,
    help="Experiment directory root.",
)
@click.option(
    "--keep-per-model",
    type=click.IntRange(min=0),
    default=1,
    show_default=True,
    help="How many latest experiments to keep per model.",
)
@click.option(
    "--archive-dir",
    type=click.Path(path_type=Path),
    help="Archive destination. Defaults to /tmp/raidar-archive/<timestamp>.",
)
@click.option("--dry-run", is_flag=True, help="Show actions without moving files.")
def experiments_prune(
    experiments_root: Path,
    keep_per_model: int,
    archive_dir: Path | None,
    dry_run: bool,
) -> None:
    """Archive stale experiment artifacts while keeping latest experiments per model."""
    archive_root = (archive_dir or _default_archive_dir()).resolve()
    experiments_root = experiments_root.resolve()
    if not dry_run:
        archive_root.mkdir(parents=True, exist_ok=True)

    kept_counts: dict[str, int] = {}
    pruned_count = 0
    for execution_dir in _sorted_experiment_dirs(experiments_root):
        model_key = _execution_model_key(execution_dir)
        count = kept_counts.get(model_key, 0)
        if count < keep_per_model:
            kept_counts[model_key] = count + 1
            continue
        if _archive_path(execution_dir, archive_root, dry_run=dry_run):
            pruned_count += 1

    click.echo(f"archive_dir={archive_root}")
    click.echo(f"experiments_pruned={pruned_count}")


@main.group()
def harness() -> None:
    """Harness discovery and validation workflows."""


@harness.command("list")
def harness_list() -> None:
    """List supported harness adapters, rule files, and model coverage."""
    click.echo("Supported harnesses:")
    for harness_name in Harness:
        adapter_class = registry.adapter_class(harness_name)
        click.echo(
            f"  {harness_name.value:12} -> "
            f"{SYSTEM_RULES.get(harness_name.value, '(no rule mapping)')}"
        )
        click.echo(f"  {'':12} models: {adapter_class.supported_model_summary()}")


@harness.command("validate")
@click.option(
    "--harness",
    "-a",
    type=click.Choice(HARNESS_CHOICES),
    required=True,
    help="Harness to validate.",
)
@click.option(
    "--model",
    "-m",
    type=str,
    required=True,
    help="Model in provider/name format.",
)
@click.option(
    "--timeout",
    type=int,
    default=1800,
    help="Timeout used to build the agent spec.",
)
def harness_validate(
    harness: str,
    model: str,
    timeout: int,
) -> None:
    """Validate harness adapter wiring and environment requirements."""
    config = AgentSpec(
        harness=Harness(harness),
        model=ModelTarget.from_string(model),
        timeout_sec=timeout,
    )
    adapter = config.adapter()
    adapter.validate()
    runtime_keys = sorted(adapter.runtime_env().keys())

    click.echo("Harness validation passed.")
    click.echo(f"  harness: {harness}")
    click.echo(f"  model: {model}")
    click.echo(f"  harbor_harness: {adapter.harbor_harness()}")
    click.echo(f"  model_argument: {adapter.model_argument()}")
    click.echo(f"  runtime_env_keys: {', '.join(runtime_keys) if runtime_keys else '(none)'}")


@main.group()
def scenario() -> None:
    """Scenario lifecycle commands."""


def _scenario_revision_paths(scenario_root: Path) -> list[Path]:
    if not scenario_root.is_dir():
        return []
    return sorted(scenario_root.glob("v*/scenario.yaml"), key=_scenario_revision_sort_key)


def _list_scenarios_with_revisions(scenarios_root: Path) -> list[tuple[str, tuple[str, ...]]]:
    if not scenarios_root.exists():
        return []

    scenarios: list[tuple[str, tuple[str, ...]]] = []
    for scenario_root in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
        revision_paths = _scenario_revision_paths(scenario_root)
        if not revision_paths:
            continue
        scenario_def = _runner_api().load_scenario(revision_paths[-1])
        revisions = tuple(path.parent.name for path in revision_paths)
        scenarios.append((scenario_def.name, revisions))
    return sorted(scenarios, key=lambda entry: entry[0])


@scenario.command("list")
@click.option(
    "--scenarios-root",
    type=click.Path(path_type=Path),
    default=REPO_ROOT / "scenarios",
    show_default=True,
    help="Root directory containing scenario folders.",
)
def scenario_list(scenarios_root: Path) -> None:
    """List available scenarios and their revisions."""
    for scenario_id, revisions in _list_scenarios_with_revisions(scenarios_root.resolve()):
        click.echo(f"{scenario_id} | revisions: {', '.join(revisions)}")


@scenario.command("init")
@click.option(
    "--path",
    "-p",
    type=click.Path(path_type=Path),
    required=True,
    help="Directory to create the scenario in.",
)
@click.option("--name", type=str, help="Scenario name. Defaults to directory name.")
@click.option(
    "--scenario-revision",
    type=str,
    default="v001",
    help="Scenario revision directory to initialize.",
)
@click.option(
    "--starter-root",
    type=str,
    default="starter",
    help="Scenario-local starter root path.",
)
@click.option(
    "--prompt-entry",
    type=str,
    default="prompt/task.md",
    help="Primary prompt artifact path (relative to scenario revision directory).",
)
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default="medium",
    help="Scenario difficulty.",
)
@click.option("--category", type=str, default="greenfield-ui", help="Scenario category.")
@click.option("--timeout", type=int, default=1800, help="Scenario timeout in seconds.")
def scenario_init(
    path: Path,
    name: str | None,
    scenario_revision: str,
    starter_root: str,
    prompt_entry: str,
    difficulty: Literal["easy", "medium", "hard"],
    category: str,
    timeout: int,
) -> None:
    """Create a new versioned scenario descriptor with prompt artifacts and rules."""
    scenario_root = path.resolve()
    scenario_name = name or scenario_root.name
    revision_dir = scenario_root / scenario_revision
    scenario_yaml = revision_dir / "scenario.yaml"
    if scenario_yaml.exists():
        raise click.ClickException(f"Scenario already exists: {scenario_yaml}")

    (revision_dir / "rules").mkdir(parents=True, exist_ok=True)
    (revision_dir / "prompt").mkdir(parents=True, exist_ok=True)

    scenario_doc = {
        "name": scenario_name,
        "scenario_revision": scenario_revision,
        "description": f"Scenario definition for {scenario_name}",
        "difficulty": difficulty,
        "category": category,
        "timeout_sec": timeout,
        "dockerfile": "./Dockerfile",
        "test_scripts": [],
        "starter": {"root": starter_root},
        "verification": {
            "max_gate_failures": 3,
            "min_quality_score": 0.8,
            "required_commands": [
                ["bun", "run", "typecheck"],
                ["bun", "run", "lint"],
            ],
            "gates": [
                {"name": "typecheck", "command": ["bun", "run", "typecheck"]},
                {"name": "lint", "command": ["bun", "run", "lint"]},
            ],
        },
        "acceptance": {
            "deterministic_checks": [
                {
                    "type": "no_pattern",
                    "pattern": "TODO",
                    "description": "No TODO markers remain in production files",
                }
            ],
            "requirements": [],
            "llm_judge_rubric": [],
        },
        "metrics": [
            {"type": "core", "id": "functional"},
            {"type": "core", "id": "acceptance"},
            {"type": "core", "id": "verification-stability"},
            {"type": "core", "id": "execution-validity"},
            {"type": "core", "id": "resource-efficiency"},
        ],
        "prompt": {"entry": prompt_entry, "includes": []},
    }
    _write_scenario_document(scenario_yaml, scenario_doc)

    prompt_path = revision_dir / prompt_entry
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        (
            "Implement the requested feature in the starter application.\n\n"
            "Run all required verification commands before completion and "
            "report only after they pass.\n"
        ),
        encoding="utf-8",
    )

    rule_text = (
        "Follow the scenario prompt exactly. Run required verification commands before completion."
    )
    for filename in sorted(set(SYSTEM_RULES.values())):
        (revision_dir / "rules" / filename).write_text(rule_text + "\n", encoding="utf-8")

    click.echo(f"Created scenario at {scenario_yaml}")


@scenario.command("validate")
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scenario.yaml file.",
)
def scenario_validate(scenario: Path) -> None:
    """Validate a scenario document and report key configuration fields."""
    runner_api = _runner_api()
    scenario_def = runner_api.load_scenario(_resolve_scenario_yaml(scenario))
    click.echo("Scenario validation passed.")
    click.echo(f"  name: {scenario_def.name}")
    click.echo(f"  scenario_revision: {scenario_def.scenario_revision}")
    click.echo(f"  starter_root: {scenario_def.starter.root}")
    click.echo(f"  prompt_entry: {scenario_def.prompt.entry}")
    click.echo(f"  required_commands: {len(scenario_def.verification.required_commands)}")
    click.echo(f"  gates: {len(scenario_def.verification.gates)}")
    click.echo(f"  metrics: {len(scenario_def.metric_ids())}")


@scenario.command("clone-revision")
@click.option(
    "--path",
    "-p",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Path to the scenario root directory that contains revision folders.",
)
@click.option(
    "--from-revision",
    type=str,
    required=True,
    help="Source scenario revision label (for example: v001).",
)
@click.option(
    "--to-revision",
    type=str,
    help="Target scenario revision label. Defaults to the next revision after --from-revision.",
)
def scenario_clone_revision(path: Path, from_revision: str, to_revision: str | None) -> None:
    """Clone a scenario revision and update revision metadata."""
    try:
        result = _scenario_clone_api().clone_scenario_revision(
            scenario_root=path.resolve(),
            source_revision=from_revision,
            target_revision=to_revision,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Scenario revision clone completed.")
    click.echo(f"  scenario_root: {result.scenario_root}")
    click.echo(f"  source_revision: {result.source_revision}")
    click.echo(f"  target_revision: {result.target_revision}")
    click.echo(f"  scenario_yaml: {result.target_scenario_yaml}")


@main.command()
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scenario revision directory",
)
@click.option(
    "--harness",
    "-a",
    type=click.Choice(HARNESS_CHOICES),
    required=True,
    help="Harness to inject rules for",
)
@click.option(
    "--starter",
    "-r",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to a specific starter template/revision directory",
)
def inject(
    scenario: Path,
    harness: str,
    starter: Path,
) -> None:
    """Inject rules into a starter workspace for testing."""
    click.echo(f"Injecting rules for {harness}")
    rules_dir = scenario / "rules"
    result = inject_rules(rules_dir, starter, harness)
    click.echo(f"Injected: {result}")


@main.command()
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    multiple=True,
    help="Path to scenario.yaml file (repeatable)",
)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to matrix configuration YAML",
)
@click.option(
    "--selector",
    type=click.Choice(["all", "codex", "gemini", "claude"]),
    help="Generate a matrix config on the fly for the selected harness family.",
)
@click.option(
    "--timeout",
    type=click.IntRange(min=1),
    default=1800,
    show_default=True,
    help="Scenario timeout used for selector-generated matrix configs.",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=5,
    show_default=True,
    help="Repeat count used for selector-generated matrix configs.",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Repeat parallelism used for selector-generated matrix configs.",
)
@click.option(
    "--rerun-unscored",
    type=click.IntRange(min=0, max=1),
    default=0,
    show_default=True,
    help="Unscored rerun budget used for selector-generated matrix configs.",
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of parallel executions",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show matrix entries without running",
)
def matrix(
    scenario: tuple[Path, ...],
    config: Path | None,
    selector: str | None,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    rerun_unscored: int,
    parallel: int,
    dry_run: bool,
) -> None:
    """Run an experiment matrix from configuration."""
    from .matrix import (
        MatrixAgentSpec,
        MatrixConfig,
        build_selected_matrix_config,
        generate_matrix_entries,
        load_matrix_config,
    )

    scenario_paths = tuple(path.resolve() for path in scenario)
    if not scenario_paths:
        raise click.ClickException("At least one --scenario path is required.")
    if (config is None) == (selector is None):
        raise click.ClickException("Provide exactly one of --config or --selector.")
    scenario_defs = _load_matrix_scenarios(scenario_paths)

    if config is not None:
        click.echo(f"Loading matrix from {config}")
        matrix_config: MatrixConfig = load_matrix_config(config)
    else:
        click.echo(f"Generating matrix from selector '{selector}'")
        matrix_config = build_selected_matrix_config(
            selector=selector,
            timeout_sec=timeout,
            repeats=repeats,
            repeat_parallel=repeat_parallel,
            retry_void=rerun_unscored,
        )
    entries: list[MatrixAgentSpec] = generate_matrix_entries(matrix_config)
    total_entries = len(entries) * len(scenario_defs)
    click.echo(
        f"Matrix defined for {len(matrix_config.agents)} agent specs ({total_entries} experiments)"
    )

    experiment_config = matrix_config.experiment
    click.echo(
        "Experiment settings: "
        f"timeout={experiment_config.timeout_sec}s, repeats={experiment_config.repeats}, "
        "repeat_parallel="
        f"{experiment_config.repeat_parallel}, rerun_unscored={experiment_config.retry_void}"
    )

    if dry_run:
        _echo_matrix_dry_run(
            scenario_defs=scenario_defs,
            entries=entries,
            repeats=experiment_config.repeats,
        )
        return

    _cleanup_stale_harbor_before_runs()
    jobs = [
        (scenario_path, scenario_def, entry)
        for scenario_path, scenario_def in scenario_defs
        for entry in entries
    ]
    successes, failures = _run_matrix_jobs(
        jobs=jobs,
        experiment_config=experiment_config,
        parallel=parallel,
    )

    click.echo(f"Matrix completed: {successes} experiments succeeded, {failures} failed.")


def _load_matrix_scenarios(
    scenario_paths: tuple[Path, ...],
) -> list[tuple[Path, ScenarioDefinition]]:
    scenario_defs: list[tuple[Path, ScenarioDefinition]] = []
    for scenario_path in scenario_paths:
        click.echo(f"Loading scenario from {scenario_path}")
        scenario_defs.append((scenario_path, _runner_api().load_scenario(scenario_path)))
    return scenario_defs


def _echo_matrix_dry_run(
    *,
    scenario_defs: list[tuple[Path, ScenarioDefinition]],
    entries: list[object],
    repeats: int,
) -> None:
    for _scenario_path, scenario_def in scenario_defs:
        for entry in entries:
            click.echo(
                f"[dry-run] {scenario_def.name}@{scenario_def.scenario_revision}: "
                f"{entry.harness}/{entry.model} x{repeats}"
            )


def _matrix_job_options(
    *,
    scenario_path: Path,
    entry: object,
    experiment_config: object,
) -> RunCliOptions:
    return RunCliOptions(
        scenario=scenario_path,
        harness=entry.harness,
        model=entry.model,
        timeout=experiment_config.timeout_sec,
        repeats=experiment_config.repeats,
        repeat_parallel=experiment_config.repeat_parallel,
        rerun_unscored=experiment_config.retry_void,
    )


def _run_matrix_jobs(
    *,
    jobs: list[tuple[Path, ScenarioDefinition, object]],
    experiment_config: object,
    parallel: int,
) -> tuple[int, int]:
    successes = 0
    failures = 0

    def _run_matrix_job(job: tuple[Path, ScenarioDefinition, object]) -> SuiteExecutionResult:
        scenario_path, _scenario_def, entry = job
        options = _matrix_job_options(
            scenario_path=scenario_path,
            entry=entry,
            experiment_config=experiment_config,
        )
        return _execute_run_options(
            options,
            force_experiment_summary=True,
            cleanup_before_runs=False,
            echo=False,
            execution_suffix=f"{entry.harness}__{entry.model.replace('/', '-')}",
        )

    if parallel > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as executor:
            future_map = {executor.submit(_run_matrix_job, job): job for job in jobs}
            for future in concurrent.futures.as_completed(future_map):
                _scenario_path, scenario_def, entry = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    click.echo(f"[{scenario_def.name}] {entry.harness}/{entry.model} failed: {exc}")
                    failures += 1
                    continue
                successes += 1
                click.echo(
                    f"[{scenario_def.name}] {entry.harness}/{entry.model} -> {result.summary_path}"
                )
        return successes, failures

    for scenario_path, scenario_def, entry in jobs:
        click.echo(
            f"Running experiment: {scenario_def.name}@{scenario_def.scenario_revision} "
            f"{entry.harness}/{entry.model}"
        )
        try:
            result = _run_matrix_job((scenario_path, scenario_def, entry))
        except Exception as exc:
            click.echo(f"[{scenario_def.name}] {entry.harness}/{entry.model} failed: {exc}")
            failures += 1
            continue
        successes += 1
        click.echo(f"[{scenario_def.name}] experiment summary: {result.summary_path}")

    return successes, failures


@main.command()
@click.option(
    "--results",
    "-r",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to experiments directory",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "csv", "markdown"]),
    default="markdown",
    help="Output format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Output file path",
)
def report(results: Path, format: str, output: Path | None) -> None:
    """Generate a comparison report from experiment runs."""
    from .storage import (
        aggregate_results,
        export_to_csv,
        generate_comparison_report,
        load_all_runs,
    )

    click.echo(f"Loading runs from {results}")
    runs = load_all_runs(results)
    click.echo(f"Found {len(runs)} runs")

    if not runs:
        click.echo("No runs found")
        return

    if format == "csv":
        out_path = output or (results / "comparison.csv")
        export_to_csv(runs, out_path)
        click.echo(f"CSV exported to {out_path}")
    elif format == "markdown":
        report_text = generate_comparison_report(runs)
        if output:
            with open(output, "w") as f:
                f.write(report_text)
            click.echo(f"Report saved to {output}")
        else:
            click.echo(report_text)
    else:  # json
        agg = aggregate_results(runs)
        if output:
            with open(output, "w") as f:
                json.dump(agg, f, indent=2)
            click.echo(f"JSON exported to {output}")
        else:
            click.echo(json.dumps(agg, indent=2))


@main.command()
def init_matrix() -> None:
    """Create example matrix configuration file."""
    from .matrix import create_example_matrix

    output_path = Path("matrix.yaml")
    with open(output_path, "w") as f:
        f.write(create_example_matrix())
    click.echo(f"Example matrix configuration created: {output_path}")


def _echo_scenario_summary(scenario_def: ScenarioDefinition) -> None:
    runner_api = _runner_api()
    click.echo(f"Scenario: {scenario_def.name}")
    click.echo(f"Revision: {scenario_def.scenario_revision}")
    click.echo(f"Description: {scenario_def.description}")
    click.echo(f"Difficulty: {scenario_def.difficulty}")
    click.echo(f"Category: {scenario_def.category}")
    click.echo(f"Timeout: {scenario_def.timeout_sec // 60} minutes")
    click.echo(f"Evaluation Profile: {runner_api.scenario_evaluation_profile(scenario_def)}")
    click.echo(f"Metrics: {', '.join(runner_api.scenario_metrics(scenario_def))}")

    if scenario_def.verification.gates:
        gates = [g.name for g in scenario_def.verification.gates]
        click.echo(f"Quality Gates: {', '.join(gates)}")


def _echo_available_revisions(scenario_root: Path) -> None:
    revision_paths = _scenario_revision_paths(scenario_root)
    if not revision_paths:
        return
    click.echo("Available Revisions:")
    for scenario_yaml in revision_paths:
        click.echo(f"  {scenario_yaml.parent.name}: {scenario_yaml.resolve()}")


def _echo_rule_variants(scenario_dir: Path) -> None:
    rules_dir = scenario_dir / "rules"
    if not rules_dir.exists():
        return
    click.echo()
    click.echo("Rules:")
    files = sorted(f.name for f in rules_dir.iterdir() if f.is_file())
    click.echo(f"  files: {', '.join(files) if files else '(none)'}")


def _echo_visual_config(task_def: ScenarioDefinition) -> None:
    if not task_def.visual:
        return
    click.echo()
    click.echo("Visual Config:")
    click.echo(f"  Reference: {task_def.visual.reference_image}")
    click.echo(f"  Threshold: {task_def.visual.threshold}")


def _echo_acceptance_config(task_def: ScenarioDefinition) -> None:
    if not (task_def.acceptance.deterministic_checks or task_def.acceptance.llm_judge_rubric):
        return
    click.echo()
    click.echo("Acceptance Config:")
    if task_def.acceptance.deterministic_checks:
        click.echo(f"  Deterministic checks: {len(task_def.acceptance.deterministic_checks)}")
    if task_def.acceptance.llm_judge_rubric:
        click.echo(f"  LLM judge criteria: {len(task_def.acceptance.llm_judge_rubric)}")


@main.command()
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to a scenario directory or scenario.yaml file",
)
def info(scenario: Path) -> None:
    """Show scenario information and details."""
    scenario_input = scenario.resolve()
    scenario_yaml = _resolve_scenario_yaml(scenario_input)
    scenario_def = _runner_api().load_scenario(scenario_yaml)

    _echo_scenario_summary(scenario_def)
    click.echo(f"Scenario YAML: {scenario_yaml}")
    if scenario_input.is_dir() and not (scenario_input / "scenario.yaml").is_file():
        _echo_available_revisions(scenario_input)
    _echo_rule_variants(scenario_yaml.parent)
    _echo_visual_config(scenario_def)
    _echo_acceptance_config(scenario_def)


if __name__ == "__main__":
    main()
