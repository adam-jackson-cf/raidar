"""CLI entrypoint for eval orchestrator."""

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
from typing import Literal

import click
from dotenv import load_dotenv

from .harness.config import Agent, HarnessConfig, ModelTarget
from .harness.rules import SYSTEM_RULES, inject_rules
from .repeat_suite import (
    create_repeat_suite_summary,
    persist_repeat_suite,
)
from .runner import (
    RunRequest,
    ScaffoldPreflightError,
    _docker_compose_preflight_reason,
    cleanup_stale_harbor_resources,
    load_task,
    run_task,
)
from .schemas.scorecard import EvalRun
from .schemas.task import (
    ComplianceConfig,
    DeterministicCheck,
    PromptConfig,
    TaskDefinition,
    VerificationConfig,
    VerificationGate,
)
from .task_clone import clone_task_version

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ORCHESTRATOR_ROOT / ".env"
ARTIFACT_CHANGE_PREFIXES = ("executions/",)
EXECUTIONS_ROOT = REPO_ROOT / "executions"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """Eval orchestrator for testing model/harness combinations."""


AGENT_CHOICES = [agent.value for agent in Agent]
VERSION_DIR_PATTERN = re.compile(r"^v(\d+)$")


@dataclass(frozen=True, slots=True)
class RunCliOptions:
    """Normalized CLI options for task execution commands."""

    task: Path
    agent: str
    model: str
    timeout: int
    repeats: int
    repeat_parallel: int
    retry_void: int

    def resolved(self) -> RunCliOptions:
        return RunCliOptions(
            task=self.task.resolve(),
            agent=self.agent,
            model=self.model,
            timeout=self.timeout,
            repeats=self.repeats,
            repeat_parallel=self.repeat_parallel,
            retry_void=min(self.retry_void, 1),
        )


def _cleanup_stale_harbor_before_runs() -> None:
    cleanup_stale_harbor_resources(include_containers=True, include_build_processes=True)


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


def _build_repeat_request(base_request: RunRequest, repeat_index: int) -> RunRequest:
    return RunRequest(
        task=base_request.task,
        config=base_request.config,
        task_dir=base_request.task_dir,
        execution_dir=base_request.execution_dir,
        repeat_index=repeat_index,
    )


def _execute_run_request(run_request: RunRequest) -> EvalRun:
    run = run_task(run_request)
    _persist_eval_run(run)
    return run


def _execute_repeat_index(request: RunRequest, repeat_index: int) -> EvalRun:
    try:
        return _execute_run_request(_build_repeat_request(request, repeat_index))
    except ScaffoldPreflightError:
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


def _count_voided(runs: list[EvalRun]) -> int:
    return sum(1 for run in runs if run.scores.voided)


def _run_with_void_retries(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
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
    except ScaffoldPreflightError as exc:
        raise click.ClickException(
            f"Fatal scaffold preflight error. Suite aborted without retries: {exc}"
        ) from exc
    all_runs.extend(initial_runs)
    pending_batch = _count_voided(initial_runs)
    next_repeat_index += len(initial_runs)

    if pending_batch > 0 and retry_void > 0:
        retries_used = 1
        try:
            retry_runs = _execute_repeat_batch(
                request=request,
                batch_size=pending_batch,
                repeat_parallel=repeat_parallel,
                start_index=next_repeat_index,
            )
        except ScaffoldPreflightError as exc:
            raise click.ClickException(
                f"Fatal scaffold preflight error. Suite aborted without retries: {exc}"
            ) from exc
        all_runs.extend(retry_runs)
        pending_batch = _count_voided(retry_runs)

    return all_runs, retries_used, pending_batch


def _build_harness_config(options: RunCliOptions) -> HarnessConfig:
    return HarnessConfig(
        agent=Agent(options.agent),
        model=ModelTarget.from_string(options.model),
        timeout_sec=options.timeout,
    )


def _execution_id(task_name: str, task_version: str, started_at: datetime) -> str:
    task_slug = task_name.lower().replace(" ", "-")
    return f"{started_at.strftime('%Y%m%d-%H%M%SZ')}__{task_slug}__{task_version}"


def _build_run_request(
    options: RunCliOptions, task_def: TaskDefinition, execution_dir: Path
) -> RunRequest:
    config = _build_harness_config(options)
    execution_dir.mkdir(parents=True, exist_ok=True)
    return RunRequest(
        task=task_def,
        config=config,
        task_dir=options.task.parent,
        execution_dir=execution_dir,
        repeat_index=1,
    )


def _echo_run_header(options: RunCliOptions, task_name: str) -> None:
    click.echo(f"Loading task from {options.task}")
    click.echo(f"Task: {task_name}")
    click.echo(f"Agent: {options.agent}")
    click.echo(f"Model: {options.model}")
    click.echo(f"Repeats: {options.repeats}")
    click.echo(f"Repeat parallelism: {options.repeat_parallel}")
    click.echo(f"Retry void budget: {options.retry_void}")


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
    click.echo(f"Void result: {result.scores.voided}")
    if result.scores.voided:
        click.echo(f"Void reasons: {result.scores.void_reasons}")
    if result.termination_reason:
        click.echo(f"Reason: {result.termination_reason}")


def _echo_suite_result(
    suite_json_path: Path,
    summary_path: Path,
    analysis_path: Path,
    retries_used: int,
    runs: list[EvalRun],
) -> None:
    click.echo(f"Suite record: {suite_json_path}")
    click.echo(f"Repeat suite summary: {summary_path}")
    click.echo(f"Suite analysis: {analysis_path}")
    click.echo(f"Void retries used: {retries_used}")
    for run in runs:
        click.echo(
            f"Run {run.id}: voided={run.scores.voided}, "
            f"run_valid={run.scores.run_validity.passed}, "
            f"performance_gates={run.scores.performance_gates.passed}, "
            f"composite={run.scores.composite_score:.3f}, duration={run.duration_sec:.1f}s"
        )


def _execute_run_options(options: RunCliOptions, *, force_suite_summary: bool) -> None:
    resolved = options.resolved()
    _cleanup_stale_harbor_before_runs()

    task_def = load_task(resolved.task)
    started_at = datetime.now(UTC)
    execution_id = _execution_id(task_def.name, task_def.version, started_at)
    execution_dir = EXECUTIONS_ROOT / execution_id
    request = _build_run_request(resolved, task_def, execution_dir)
    _echo_run_header(resolved, request.task.name)

    click.echo("Running task...")
    try:
        runs, retries_used, unresolved_void = _run_with_void_retries(
            request=request,
            repeats=max(1, resolved.repeats),
            repeat_parallel=resolved.repeat_parallel,
            retry_void=resolved.retry_void,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if resolved.repeats == 1 and not force_suite_summary:
        _echo_single_run_result(runs[0])
        return

    suite_summary = create_repeat_suite_summary(
        task_name=request.task.name,
        harness=resolved.agent,
        model=resolved.model,
        repeats=resolved.repeats,
        repeat_parallel=max(1, min(resolved.repeat_parallel, resolved.repeats)),
        runs=runs,
        started_at=started_at,
        retry_void_limit=resolved.retry_void,
        retries_used=retries_used,
        unresolved_void_count=unresolved_void,
    )
    suite_json_path, summary_path, analysis_path = persist_repeat_suite(
        execution_dir, suite_summary
    )
    _echo_suite_result(suite_json_path, summary_path, analysis_path, retries_used, runs)


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


@main.command()
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task.yaml file",
)
@click.option(
    "--agent",
    "-a",
    type=click.Choice(AGENT_CHOICES),
    required=True,
    help="Agent/harness to use",
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
    help="Task timeout in seconds",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=1,
    help="Number of repeated runs for the same configuration",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    help="Parallel workers for repeat runs",
)
@click.option(
    "--retry-void",
    type=click.IntRange(min=0, max=1),
    default=0,
    help="Retry budget for voided runs (0 or 1; at most one retry per failure)",
)
def run(
    task: Path,
    agent: str,
    model: str,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
) -> None:
    """Run a task with specified harness and model."""
    options = RunCliOptions(
        task=task,
        agent=agent,
        model=model,
        timeout=timeout,
        repeats=repeats,
        repeat_parallel=repeat_parallel,
        retry_void=retry_void,
    )
    _execute_run_options(options, force_suite_summary=False)


@main.group()
def suite() -> None:
    """Suite-level run workflows."""


@suite.command("run")
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task.yaml file",
)
@click.option(
    "--agent",
    "-a",
    type=click.Choice(AGENT_CHOICES),
    required=True,
    help="Agent/harness to use",
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
    help="Task timeout in seconds",
)
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=5,
    help="Number of repeated runs in the suite",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    help="Parallel workers for repeat runs",
)
@click.option(
    "--retry-void",
    type=click.IntRange(min=0, max=1),
    default=1,
    help="Retry budget for voided runs (0 or 1)",
)
def suite_run(
    task: Path,
    agent: str,
    model: str,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
) -> None:
    """Run repeat suites with deterministic aggregate output."""
    options = RunCliOptions(
        task=task,
        agent=agent,
        model=model,
        timeout=timeout,
        repeats=repeats,
        repeat_parallel=repeat_parallel,
        retry_void=retry_void,
    )
    _execute_run_options(options, force_suite_summary=True)


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
    _run_or_raise([sys.executable, "-m", "pytest", "tests", "-x", "--tb=short"], ORCHESTRATOR_ROOT)

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
    cleanup_stale_harbor_resources(
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

    reason = _docker_compose_preflight_reason(dict(os.environ))
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
def provider() -> None:
    """Provider and adapter workflows."""


@provider.command("list")
def provider_list() -> None:
    """List supported provider CLI adapters and rule files."""
    click.echo("Supported providers:")
    for agent in AGENT_CHOICES:
        click.echo(f"  {agent:12} -> {SYSTEM_RULES.get(agent, '(no rule mapping)')}")


@provider.command("validate")
@click.option(
    "--agent",
    "-a",
    type=click.Choice(AGENT_CHOICES),
    required=True,
    help="Agent/harness to validate.",
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
    help="Timeout used to build harness config.",
)
def provider_validate(
    agent: str,
    model: str,
    timeout: int,
) -> None:
    """Validate provider adapter wiring and environment requirements."""
    config = HarnessConfig(
        agent=Agent(agent),
        model=ModelTarget.from_string(model),
        timeout_sec=timeout,
    )
    adapter = config.adapter()
    adapter.validate()
    runtime_keys = sorted(adapter.runtime_env().keys())

    click.echo("Provider validation passed.")
    click.echo(f"  agent: {agent}")
    click.echo(f"  model: {model}")
    click.echo(f"  harbor_agent: {adapter.harbor_agent()}")
    click.echo(f"  model_argument: {adapter.model_argument()}")
    click.echo(f"  runtime_env_keys: {', '.join(runtime_keys) if runtime_keys else '(none)'}")


@main.group()
def task() -> None:
    """Task lifecycle commands."""


@task.command("init")
@click.option(
    "--path",
    "-p",
    type=click.Path(path_type=Path),
    required=True,
    help="Directory to create the task in.",
)
@click.option("--name", type=str, help="Task name. Defaults to directory name.")
@click.option(
    "--task-version",
    type=str,
    default="v001",
    help="Task version directory to initialize.",
)
@click.option(
    "--scaffold-root",
    type=str,
    default="scaffold",
    help="Task-local scaffold root path.",
)
@click.option(
    "--prompt-entry",
    type=str,
    default="prompt/task.md",
    help="Primary prompt artifact path (relative to task version directory).",
)
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default="medium",
    help="Task difficulty.",
)
@click.option("--category", type=str, default="greenfield-ui", help="Task category.")
@click.option("--timeout", type=int, default=1800, help="Task timeout in seconds.")
def task_init(
    path: Path,
    name: str | None,
    task_version: str,
    scaffold_root: str,
    prompt_entry: str,
    difficulty: Literal["easy", "medium", "hard"],
    category: str,
    timeout: int,
) -> None:
    """Create a new versioned task descriptor with prompt artifacts and rules."""
    task_root = path.resolve()
    task_name = name or task_root.name
    task_dir = task_root / task_version
    task_yaml = task_dir / "task.yaml"
    if task_yaml.exists():
        raise click.ClickException(f"Task already exists: {task_yaml}")

    (task_dir / "rules").mkdir(parents=True, exist_ok=True)
    (task_dir / "prompt").mkdir(parents=True, exist_ok=True)

    task_def = TaskDefinition(
        name=task_name,
        version=task_version,
        description=f"Task definition for {task_name}",
        difficulty=difficulty,
        category=category,
        timeout_sec=timeout,
        scaffold={"root": scaffold_root},
        verification=VerificationConfig(
            max_gate_failures=3,
            min_quality_score=0.8,
            required_commands=[
                ["bun", "run", "typecheck"],
                ["bun", "run", "lint"],
            ],
            gates=[
                VerificationGate(name="typecheck", command=["bun", "run", "typecheck"]),
                VerificationGate(name="lint", command=["bun", "run", "lint"]),
            ],
        ),
        compliance=ComplianceConfig(
            deterministic_checks=[
                DeterministicCheck(
                    type="no_pattern",
                    pattern="TODO",
                    description="No TODO markers remain in production files",
                )
            ]
        ),
        prompt=PromptConfig(entry=prompt_entry, includes=[]),
    )
    task_dir.mkdir(parents=True, exist_ok=True)
    task_def.to_yaml(task_yaml)

    prompt_path = task_dir / prompt_entry
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        (
            "Implement the requested feature in the scaffold application.\n\n"
            "Run all required verification commands before completion and "
            "report only after they pass.\n"
        ),
        encoding="utf-8",
    )

    rule_text = (
        "Follow the task prompt exactly. Run required verification commands before completion."
    )
    for filename in sorted(set(SYSTEM_RULES.values())):
        (task_dir / "rules" / filename).write_text(rule_text + "\n", encoding="utf-8")

    click.echo(f"Created task at {task_yaml}")


@task.command("validate")
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task.yaml file.",
)
def task_validate(task: Path) -> None:
    """Validate task schema and report key configuration fields."""
    task_def = load_task(task.resolve())
    click.echo("Task validation passed.")
    click.echo(f"  name: {task_def.name}")
    click.echo(f"  version: {task_def.version}")
    click.echo(f"  scaffold_root: {task_def.scaffold.root}")
    click.echo(f"  prompt_entry: {task_def.prompt.entry}")
    click.echo(f"  required_commands: {len(task_def.verification.required_commands)}")
    click.echo(f"  gates: {len(task_def.verification.gates)}")


@task.command("clone-version")
@click.option(
    "--path",
    "-p",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Path to task root directory that contains version folders.",
)
@click.option(
    "--from-version",
    type=str,
    required=True,
    help="Source task version label (for example: v001).",
)
@click.option(
    "--to-version",
    type=str,
    help="Target task version label. Defaults to the next version after --from-version.",
)
def task_clone_version(path: Path, from_version: str, to_version: str | None) -> None:
    """Clone a task version and update task/scaffold version metadata."""
    try:
        result = clone_task_version(
            task_root=path.resolve(),
            source_version=from_version,
            target_version=to_version,
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo("Task version clone completed.")
    click.echo(f"  task_root: {result.task_root}")
    click.echo(f"  source_version: {result.source_version}")
    click.echo(f"  target_version: {result.target_version}")
    click.echo(f"  task_yaml: {result.target_task_yaml}")
    click.echo(f"  scaffold_manifest: {result.target_scaffold_manifest}")


@main.command()
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to a specific scaffold template/version directory",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Output path for manifest (default: scaffold/scaffold.manifest.json)",
)
def manifest(scaffold: Path, output: Path | None) -> None:
    """Generate scaffold manifest."""
    from .audit.scaffold_manifest import generate_manifest, save_manifest

    click.echo(f"Generating manifest for {scaffold}")
    m = generate_manifest(scaffold)

    output_path = output or (scaffold / "scaffold.manifest.json")
    save_manifest(m, output_path)
    click.echo(f"Manifest saved to {output_path}")


@main.command()
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task directory",
)
@click.option(
    "--agent",
    "-a",
    type=click.Choice(sorted(set(AGENT_CHOICES + ["copilot", "cursor", "pi"]))),
    required=True,
    help="Agent to inject rules for",
)
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to a specific scaffold template/version directory",
)
def inject(
    task: Path,
    agent: str,
    scaffold: Path,
) -> None:
    """Inject rules into scaffold for testing."""
    click.echo(f"Injecting rules for {agent}")
    rules_dir = task / "rules"
    result = inject_rules(rules_dir, scaffold, agent)
    click.echo(f"Injected: {result}")


@main.command()
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    multiple=True,
    help="Path to task.yaml file (repeatable)",
)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to matrix configuration YAML",
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
def matrix(task: tuple[Path, ...], config: Path, parallel: int, dry_run: bool) -> None:
    """Run evaluation matrix from configuration."""
    _cleanup_stale_harbor_before_runs()
    from .comparison.matrix_runner import MatrixRunner
    from .matrix import MatrixEntry, generate_matrix_entries, load_matrix_config

    task_paths = tuple(path.resolve() for path in task)
    if not task_paths:
        raise click.ClickException("At least one --task path is required.")
    task_defs = _load_matrix_tasks(task_paths)

    click.echo(f"Loading matrix from {config}")
    matrix_config = load_matrix_config(config)
    entries: list[MatrixEntry] = generate_matrix_entries(matrix_config)
    total_entries = len(entries)
    click.echo(
        f"Matrix defined for {len(matrix_config.runs)} harness/model pairs ({total_entries} runs)"
    )

    runner = MatrixRunner(
        tasks_dir=task_paths[0].parent,
        executions_dir=Path(matrix_config.executions_path),
    )

    if len(task_defs) == 1:
        _run_single_task_matrix(
            runner=runner,
            task_defs=task_defs,
            matrix_config=matrix_config,
            parallel=parallel,
            dry_run=dry_run,
        )
        return

    _run_multi_task_matrix(
        runner=runner,
        task_defs=task_defs,
        entries=entries,
        parallel=parallel,
        dry_run=dry_run,
    )


def _load_matrix_tasks(task_paths: tuple[Path, ...]) -> list[tuple[Path, object]]:
    task_defs: list[tuple[Path, object]] = []
    for task_path in task_paths:
        click.echo(f"Loading task from {task_path}")
        task_defs.append((task_path, load_task(task_path)))
    return task_defs


def _run_single_task_matrix(
    *,
    runner,
    task_defs: list[tuple[Path, object]],
    matrix_config,
    parallel: int,
    dry_run: bool,
) -> None:
    task_path, task_def = task_defs[0]
    report = runner.run_matrix(
        task=task_def,
        matrix_config=matrix_config,
        parallel=parallel,
        dry_run=dry_run,
    )
    click.echo(
        f"Matrix completed ({task_path.name}): "
        f"{report.successful_runs} successes, {report.failed_runs} failures."
    )


def _run_multi_task_matrix(
    *,
    runner,
    task_defs: list[tuple[Path, object]],
    entries,
    parallel: int,
    dry_run: bool,
) -> None:
    configs = [entry.to_harness_config() for entry in entries]
    jobs = [(task_path, task_def, cfg) for task_path, task_def in task_defs for cfg in configs]
    click.echo(
        f"Running multi-task matrix: {len(task_defs)} tasks × {len(configs)} configs = "
        f"{len(jobs)} runs with parallel={parallel}"
    )
    successes = 0
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as executor:
        future_to_task = {
            executor.submit(
                runner.run_single,
                task_def,
                cfg,
                task_path.parent,
                dry_run,
            ): task_path
            for task_path, task_def, cfg in jobs
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task_path = future_to_task[future]
            try:
                result = future.result()
            except Exception as exc:
                click.echo(f"[{task_path.stem}] failed: {exc}")
                failures += 1
                continue
            if result.scorecard is not None:
                successes += 1
            elif result.error is not None:
                failures += 1
    click.echo(f"Multi-task matrix completed: {successes} successes, {failures} failures.")


@main.command()
@click.option(
    "--results",
    "-r",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to executions directory",
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
    """Generate comparison report from executions."""
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


def _echo_task_summary(task_def: TaskDefinition) -> None:
    click.echo(f"Task: {task_def.name}")
    click.echo(f"Version: {task_def.version}")
    click.echo(f"Description: {task_def.description}")
    click.echo(f"Difficulty: {task_def.difficulty}")
    click.echo(f"Category: {task_def.category}")
    click.echo(f"Timeout: {task_def.timeout_sec // 60} minutes")

    if task_def.verification.gates:
        gates = [g.name for g in task_def.verification.gates]
        click.echo(f"Quality Gates: {', '.join(gates)}")


def _echo_rule_variants(task_dir: Path) -> None:
    rules_dir = task_dir / "rules"
    if not rules_dir.exists():
        return
    click.echo()
    click.echo("Rules:")
    files = sorted(f.name for f in rules_dir.iterdir() if f.is_file())
    click.echo(f"  files: {', '.join(files) if files else '(none)'}")


def _echo_visual_config(task_def: TaskDefinition) -> None:
    if not task_def.visual:
        return
    click.echo()
    click.echo("Visual Config:")
    click.echo(f"  Reference: {task_def.visual.reference_image}")
    click.echo(f"  Threshold: {task_def.visual.threshold}")


def _echo_compliance_config(task_def: TaskDefinition) -> None:
    if not (task_def.compliance.deterministic_checks or task_def.compliance.llm_judge_rubric):
        return
    click.echo()
    click.echo("Compliance Config:")
    if task_def.compliance.deterministic_checks:
        click.echo(f"  Deterministic checks: {len(task_def.compliance.deterministic_checks)}")
    if task_def.compliance.llm_judge_rubric:
        click.echo(f"  LLM judge criteria: {len(task_def.compliance.llm_judge_rubric)}")


def _task_version_sort_key(task_yaml: Path) -> tuple[int, str]:
    version_dir = task_yaml.parent.name
    match = VERSION_DIR_PATTERN.fullmatch(version_dir)
    if match is None:
        return (-1, version_dir)
    return (int(match.group(1)), version_dir)


@main.command()
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task directory",
)
def info(task: Path) -> None:
    """Show task information and details."""
    task_yaml = task / "task.yaml"
    if not task_yaml.exists():
        candidates = list(task.glob("v*/task.yaml"))
        if not candidates:
            click.echo(f"Error: task.yaml not found in {task}", err=True)
            raise SystemExit(1)
        task_yaml = max(candidates, key=_task_version_sort_key)
        task = task_yaml.parent

    task_def = TaskDefinition.from_yaml(task_yaml)

    _echo_task_summary(task_def)
    _echo_rule_variants(task)
    _echo_visual_config(task_def)
    _echo_compliance_config(task_def)


if __name__ == "__main__":
    main()
