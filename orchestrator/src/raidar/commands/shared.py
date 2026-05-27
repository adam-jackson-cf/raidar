"""Shared helpers for CLI command modules."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from raidar.application.execution import (
    BENCHMARK_EXPERIMENTS_ROOT_NAME,
    RESEARCH_LOOP_EXPERIMENTS_ROOT_NAME,
    execute_run_command,
)
from raidar.application.execution import (
    build_run_cli_options_from_request as _service_build_run_cli_options,
)
from raidar.application.execution import (
    experiment_execution_suffix as _service_experiment_execution_suffix,
)
from raidar.application.execution import (
    resolve_experiments_root as _service_resolve_experiments_root,
)
from raidar.application.models import (
    ExecutionDispatchRequest,
    RunCliOptions,
    RunCliOptionsBuildRequest,
    SuiteExecutionResult,
)
from raidar.application.scenarios import (
    resolve_scenario_yaml as _service_resolve_scenario_yaml,
)
from raidar.application.serializers import (
    suite_execution_payload as _service_suite_execution_payload,
)
from raidar.runtime.maintenance import cleanup_stale_harbor_resources

REPO_ROOT = Path(__file__).resolve().parents[4]
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = ORCHESTRATOR_ROOT / ".env"
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"
BENCHMARK_EXPERIMENTS_ROOT = EXPERIMENTS_ROOT / BENCHMARK_EXPERIMENTS_ROOT_NAME
RESEARCH_LOOP_EXPERIMENTS_ROOT = EXPERIMENTS_ROOT / RESEARCH_LOOP_EXPERIMENTS_ROOT_NAME
DEFAULT_ARCHIVE_ROOT = Path("/tmp")
EXPERIMENT_KIND_CHOICES = ["benchmark", "research-loop"]
HARNESS_CHOICES: list[str] = []


def set_harness_choices(choices: list[str]) -> None:
    HARNESS_CHOICES[:] = choices


def cleanup_stale_harbor_before_runs() -> None:
    cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )


def experiment_execution_suffix(options: RunCliOptions) -> str:
    return _service_experiment_execution_suffix(options)


def resolve_experiments_root(
    *,
    experiments_root: Path | None,
    experiment_kind: str | None,
) -> Path:
    return _service_resolve_experiments_root(
        experiments_root=experiments_root,
        experiment_kind=experiment_kind,
        repo_root=REPO_ROOT,
    )


def suite_execution_payload(result: SuiteExecutionResult) -> dict[str, object]:
    return _service_suite_execution_payload(result)


def build_run_options_from_mapping(options: dict[str, object]) -> RunCliOptions:
    return _service_build_run_cli_options(
        RunCliOptionsBuildRequest(
            scenario=options["scenario"],
            harness=options["harness"],
            provider=options["provider"],
            model=options["model"],
            reasoning_effort=options.get("reasoning_effort"),
            timeout=options["timeout"],
            repeats=options["repeats"],
            repeat_parallel=options["repeat_parallel"],
            rerun_unscored=options["rerun_unscored"],
            experiments_root=options.get("experiments_root"),
            experiment_kind=options.get("experiment_kind"),
            repo_root=REPO_ROOT,
        )
    )


def execute_run_options(
    options: RunCliOptions,
    **settings: Any,
) -> SuiteExecutionResult:
    return execute_run_command(
        ExecutionDispatchRequest(
            options=options,
            force_experiment_summary=settings["force_experiment_summary"],
            cleanup_before_runs=settings["cleanup_before_runs"],
            echo=settings["echo"],
            execution_suffix=settings.get("execution_suffix"),
        ),
        repo_root=REPO_ROOT,
    )


def run_or_raise(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(cmd)
    click.echo(f"[exec] {rendered}")
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise click.ClickException(f"Command failed ({result.returncode}): {rendered}")


def load_json_file(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def resolve_scenario_yaml(path: Path) -> Path:
    try:
        return _service_resolve_scenario_yaml(path)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def python_cmd() -> str:
    return sys.executable
