"""CLI entrypoint for the scenario/experiment orchestrator."""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from dotenv import load_dotenv

from .agents.adapters.base import resolve_cli_executable
from .agents.adapters.codex_auth import (
    CODEX_AUTH_MODE_ENV,
    codex_auth_json_path,
    has_file_backed_codex_auth,
)
from .agents.adapters.factory import adapter_class_for_harness, resolve_adapter
from .agents.config import AgentSpec, Harness, ModelTarget
from .agents.rules import SYSTEM_RULES, inject_rules
from .application import repo_state
from .application.execution import (
    build_run_cli_options_from_request as _service_build_run_cli_options,
)
from .application.execution import (
    execute_run_command,
)
from .application.execution import (
    experiment_execution_suffix as _service_experiment_execution_suffix,
)
from .application.execution import resolve_experiments_root as _service_resolve_experiments_root
from .application.models import (
    ExecutionDispatchRequest,
    RunCliOptions,
    RunCliOptionsBuildRequest,
    ScenarioCloneRequest,
    ScenarioInitRequest,
    SuiteExecutionResult,
)
from .application.scenario_catalog import (
    load_scenario,
    scenario_evaluation_profile,
    scenario_metrics,
)
from .application.scenarios import (
    clone_scenario_revision as _service_clone_scenario_revision,
)
from .application.scenarios import (
    init_scenario as _service_init_scenario,
)
from .application.scenarios import (
    resolve_scenario_yaml as _service_resolve_scenario_yaml,
)
from .application.scenarios import (
    scenario_revision_paths as _service_scenario_revision_paths,
)
from .application.scenarios import (
    validate_scenario as _service_validate_scenario,
)
from .application.serializers import (
    scenario_clone_payload as _scenario_clone_payload,
)
from .application.serializers import (
    scenario_init_payload as _scenario_init_payload,
)
from .application.serializers import (
    suite_execution_payload as _service_suite_execution_payload,
)
from .runtime.maintenance import (
    cleanup_stale_harbor_resources,
    docker_compose_preflight_reason,
)

if TYPE_CHECKING:
    from .schemas.scenario import ScenarioDefinition

REPO_ROOT = Path(__file__).resolve().parents[3]
ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ORCHESTRATOR_ROOT / ".env"
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"
BENCHMARK_EXPERIMENTS_ROOT = EXPERIMENTS_ROOT / "benchmarks"
RESEARCH_LOOP_EXPERIMENTS_ROOT = EXPERIMENTS_ROOT / "research_loops"
DEFAULT_ARCHIVE_ROOT = Path("/tmp")
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(package_name="raidar")
def main() -> None:
    """Scenario/experiment orchestrator for harness/model evaluation runs."""


HARNESS_CHOICES = [harness.value for harness in Harness]
EXPERIMENT_KIND_CHOICES = ["benchmark", "research-loop"]
INTEGRATION_TEST_TARGET = "tests/test_runner_harbor_env_and_cleanup.py"
TYPECHECK_TARGETS = [
    "src/raidar/watcher",
    "src/raidar/agents/adapters",
    "tests/test_codex_cli_adapter.py",
    "tests/test_claude_code_cli_adapter.py",
    "tests/test_gemini_cli_adapter.py",
]
COVERAGE_FAIL_UNDER = "60"


def _scenario_clone_api() -> Any:
    from . import scenario_clone as scenario_clone_module

    return scenario_clone_module


def _cleanup_stale_harbor_before_runs() -> None:
    cleanup_stale_harbor_resources(
        include_containers=True,
        include_build_processes=True,
    )


def _experiment_execution_suffix(options: RunCliOptions) -> str:
    return _service_experiment_execution_suffix(options)


def _resolve_experiments_root(
    *,
    experiments_root: Path | None,
    experiment_kind: str | None,
) -> Path:
    return _service_resolve_experiments_root(
        experiments_root=experiments_root,
        experiment_kind=experiment_kind,
        repo_root=REPO_ROOT,
    )


def _suite_execution_payload(result: SuiteExecutionResult) -> dict[str, object]:
    return _service_suite_execution_payload(result)


def _build_run_options_from_mapping(options: dict[str, object]) -> RunCliOptions:
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


def _execute_run_options(
    options: RunCliOptions,
    **settings,
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


def _run_or_raise(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(cmd)
    click.echo(f"[exec] {rendered}")
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
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


def _resolve_scenario_yaml(path: Path) -> Path:
    try:
        return _service_resolve_scenario_yaml(path)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


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
    filters: dict[str, object],
) -> bool:
    scenario_value = str(record.get("scenario_name", "")).lower()
    model_value = str(record.get("model", "")).lower()
    harness_value = str(record.get("harness", "")).lower()
    evaluation_profile_value = str(record.get("evaluation_profile", "")).lower()
    scenario = filters.get("scenario")
    model = filters.get("model")
    harness = filters.get("harness")
    evaluation_profile = filters.get("evaluation_profile")
    if isinstance(scenario, str) and scenario.lower() not in scenario_value:
        return False
    if isinstance(model, str) and model.lower() not in model_value:
        return False
    if isinstance(harness, str) and harness.lower() not in harness_value:
        return False
    return not (
        isinstance(evaluation_profile, str)
        and evaluation_profile.lower() not in evaluation_profile_value
    )


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
    help="Model identifier within provider (e.g., gpt-5.4-mini)",
)
@click.option(
    "--provider",
    "-p",
    type=str,
    required=True,
    help="Upstream model provider (openai, anthropic, google).",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
    help="Optional normalized reasoning/thinking effort for supported models.",
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
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
    help="Experiment storage kind.",
)
@click.option(
    "--experiments-root",
    type=click.Path(path_type=Path),
    help="Override experiment directory root.",
)
def run(**options) -> None:
    """Run one scenario with the specified harness and model for smoke/debug workflows."""
    run_options = _build_run_options_from_mapping(options)
    _execute_run_options(
        run_options,
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
    help="Model identifier within provider.",
)
@click.option(
    "--provider",
    "-p",
    type=str,
    required=True,
    help="Upstream model provider (openai, anthropic, google).",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
    help="Optional normalized reasoning/thinking effort for supported models.",
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
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
    help="Experiment storage kind.",
)
@click.option(
    "--experiments-root",
    type=click.Path(path_type=Path),
    help="Override experiment directory root.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def experiment_run(**options) -> None:
    """Run a repeated experiment with deterministic aggregate output."""
    run_options = _build_run_options_from_mapping(options)
    result = _execute_run_options(
        run_options,
        force_experiment_summary=True,
        cleanup_before_runs=True,
        echo=not options["as_json"],
        execution_suffix=_experiment_execution_suffix(run_options),
    )
    if options["as_json"]:
        click.echo(json.dumps(_suite_execution_payload(result), indent=2))


@main.group()
def quality() -> None:
    """Quality gate commands."""


def _validate_quality_gate_options(*, fix: bool, stage: bool) -> None:
    if stage and not fix:
        raise click.ClickException("--stage is only supported together with --fix.")
    if fix and repo_state.has_unstaged_changes(REPO_ROOT):
        raise click.ClickException(
            "Unstaged changes detected. Stage or stash before running --fix."
        )


def _assert_quality_gate_requirements() -> None:
    repo_state.assert_no_generated_artifact_changes(REPO_ROOT)
    if shutil.which("lizard") is None:
        raise click.ClickException("Missing required command: lizard")


def _run_ruff_quality_gates(*, fix: bool) -> None:
    if fix:
        _run_or_raise(
            [sys.executable, "-m", "ruff", "format", "--force-exclude"], ORCHESTRATOR_ROOT
        )
        _run_or_raise(
            [sys.executable, "-m", "ruff", "check", ".", "--fix", "--force-exclude"],
            ORCHESTRATOR_ROOT,
        )
        return

    _run_or_raise(
        [sys.executable, "-m", "ruff", "format", "--check", "--force-exclude"],
        ORCHESTRATOR_ROOT,
    )
    _run_or_raise(
        [sys.executable, "-m", "ruff", "check", ".", "--no-fix", "--force-exclude"],
        ORCHESTRATOR_ROOT,
    )


def _coverage_env() -> dict[str, str]:
    coverage_dir = ORCHESTRATOR_ROOT / ".pytest_cache" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage_env = dict(os.environ)
    coverage_env["COVERAGE_FILE"] = str(coverage_dir / ".coverage")
    return coverage_env


def _run_orchestrator_quality_gates() -> None:
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
        env=_coverage_env(),
    )


@quality.command("gates")
@click.option("--fix", is_flag=True, help="Apply auto-fixes where supported.")
@click.option("--stage", is_flag=True, help="Stage tracked file updates after fixes.")
def quality_gates(fix: bool, stage: bool) -> None:
    """Run deterministic quality gates for orchestrator source."""
    _validate_quality_gate_options(fix=fix, stage=stage)
    _assert_quality_gate_requirements()
    _run_ruff_quality_gates(fix=fix)
    _run_orchestrator_quality_gates()

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
    help="Additional argument to append to the frozen `uv sync` invocation.",
)
def env_setup(install_tools: bool, sync_arg: tuple[str, ...]) -> None:
    """Setup local toolchain and run Harbor preflight checks."""
    _cleanup_stale_harbor_before_runs()

    reason = docker_compose_preflight_reason(dict(os.environ))
    if reason:
        raise click.ClickException(reason)

    if install_tools:
        _run_or_raise(["uv", "python", "install", "3.12"], ORCHESTRATOR_ROOT)
        _run_or_raise(["uv", "sync", "--frozen", *sync_arg], ORCHESTRATOR_ROOT)
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
    default=None,
    help="Experiment directory root.",
)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
    help="Experiment storage kind.",
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
def experiments_list(**options) -> None:
    """List experiments with optional filters."""
    resolved_root = _resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    dirs = _sorted_experiment_dirs(resolved_root)
    rows: list[dict[str, object]] = []
    for path in dirs:
        record = _execution_record(path)
        if not _execution_matches_filters(record, options):
            continue
        rows.append(record)
        if len(rows) >= options["limit"]:
            break

    if options["as_json"]:
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
    default=None,
    help="Experiment directory root.",
)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
    help="Experiment storage kind.",
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
def experiments_prune(**options) -> None:
    """Archive stale experiment artifacts while keeping latest experiments per model."""
    archive_root = (options["archive_dir"] or _default_archive_dir()).resolve()
    experiments_root = _resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    if not options["dry_run"]:
        archive_root.mkdir(parents=True, exist_ok=True)

    kept_counts: dict[str, int] = {}
    pruned_count = 0
    for execution_dir in _sorted_experiment_dirs(experiments_root):
        model_key = _execution_model_key(execution_dir)
        count = kept_counts.get(model_key, 0)
        if count < options["keep_per_model"]:
            kept_counts[model_key] = count + 1
            continue
        if _archive_path(execution_dir, archive_root, dry_run=options["dry_run"]):
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
        adapter_class = adapter_class_for_harness(harness_name)
        click.echo(
            f"  {harness_name.value:12} -> {SYSTEM_RULES.get(harness_name, '(no rule mapping)')}"
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
    help="Model identifier within provider.",
)
@click.option(
    "--provider",
    "-p",
    type=str,
    required=True,
    help="Upstream model provider (openai, anthropic, google).",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
    help="Optional normalized reasoning/thinking effort for supported models.",
)
@click.option(
    "--timeout",
    type=int,
    default=1800,
    help="Timeout used to build the agent spec.",
)
def harness_validate(**options) -> None:
    """Validate harness adapter wiring and environment requirements."""
    config = AgentSpec(
        harness=Harness(options["harness"]),
        model=ModelTarget(
            provider=options["provider"],
            name=options["model"],
            reasoning_effort=options["reasoning_effort"],
        ),
        timeout_sec=options["timeout"],
    )
    adapter = resolve_adapter(config)
    adapter.validate()
    runtime_keys = sorted(adapter.runtime_env().keys())

    click.echo("Harness validation passed.")
    click.echo(f"  harness: {options['harness']}")
    click.echo(f"  provider: {options['provider']}")
    click.echo(f"  model: {options['model']}")
    if options["reasoning_effort"] is not None:
        click.echo(f"  reasoning_effort: {options['reasoning_effort']}")
    click.echo(f"  harbor_harness: {adapter.harbor_harness()}")
    click.echo(f"  model_argument: {adapter.model_argument()}")
    for key, value in adapter.execution_metadata().items():
        if value is not None:
            click.echo(f"  {key}: {value}")
    click.echo(f"  runtime_env_keys: {', '.join(runtime_keys) if runtime_keys else '(none)'}")


@harness.command("setup-auth")
@click.option(
    "--harness",
    "-a",
    type=click.Choice(HARNESS_CHOICES),
    required=True,
    help="Harness to set up auth for.",
)
@click.option(
    "--device-auth",
    is_flag=True,
    help="Use Codex device-code login instead of the browser callback flow.",
)
def harness_setup_auth(
    harness: str,
    device_auth: bool,
) -> None:
    """Set up harness authentication for supported interactive providers."""
    if harness != Harness.CODEX_CLI.value:
        raise click.ClickException(
            f"Harness auth setup is only implemented for {Harness.CODEX_CLI.value}."
        )

    auth_path = codex_auth_json_path()
    if has_file_backed_codex_auth(auth_path):
        click.echo("Codex auth is already configured.")
        click.echo("  auth_mode: chatgpt")
        click.echo(f"  auth_json_path: {auth_path}")
        return

    command = [
        resolve_cli_executable(
            cli_env_var="CODEX_CLI_PATH",
            default_binary="codex",
            harness_label=Harness.CODEX_CLI.value,
        ),
        "login",
    ]
    if device_auth:
        command.append("--device-auth")

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise click.ClickException(
            f"Codex login failed with exit code {completed.returncode}. "
            "Resolve the login error and retry `make codex-auth-setup`."
        )

    if not has_file_backed_codex_auth(auth_path):
        raise click.ClickException(
            "Codex login completed but no file-backed auth.json was found. "
            f"Configure Codex to use file-backed credentials in {auth_path.parent} "
            f'(for example via `cli_auth_credentials_store = "file"`) and retry. '
            f"{CODEX_AUTH_MODE_ENV}=chatgpt requires file-backed auth."
        )

    click.echo("Codex auth setup complete.")
    click.echo("  auth_mode: chatgpt")
    click.echo(f"  auth_json_path: {auth_path}")


@main.group()
def scenario() -> None:
    """Scenario lifecycle commands."""


def _scenario_revision_paths(scenario_root: Path) -> list[Path]:
    return _service_scenario_revision_paths(scenario_root)


def _list_scenarios_with_revisions(scenarios_root: Path) -> list[tuple[str, tuple[str, ...]]]:
    if not scenarios_root.exists():
        return []

    scenarios: list[tuple[str, tuple[str, ...]]] = []
    for scenario_root in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
        revision_paths = _scenario_revision_paths(scenario_root)
        if not revision_paths:
            continue
        scenario_def = load_scenario(revision_paths[-1])
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
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def scenario_init(**options) -> None:
    """Create a new versioned scenario descriptor with prompt artifacts and rules."""
    try:
        result = _service_init_scenario(
            ScenarioInitRequest(
                path=options["path"],
                name=options["name"],
                scenario_revision=options["scenario_revision"],
                starter_root=options["starter_root"],
                prompt_entry=options["prompt_entry"],
                difficulty=options["difficulty"],
                category=options["category"],
                timeout_sec=options["timeout"],
            )
        )
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    if options["as_json"]:
        click.echo(json.dumps(_scenario_init_payload(result), indent=2))
        return
    click.echo(f"Created scenario at {result.scenario_yaml}")


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
    try:
        result = _service_validate_scenario(scenario)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    scenario_def = result.scenario
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
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def scenario_clone_revision(
    path: Path, from_revision: str, to_revision: str | None, as_json: bool
) -> None:
    """Clone a scenario revision and update revision metadata."""
    try:
        result = _service_clone_scenario_revision(
            ScenarioCloneRequest(
                path=path,
                from_revision=from_revision,
                to_revision=to_revision,
            )
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(_scenario_clone_payload(result), indent=2))
        return
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
    result = inject_rules(rules_dir, starter, Harness(harness))
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
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
    help="Experiment storage kind.",
)
@click.option(
    "--experiments-root",
    type=click.Path(path_type=Path),
    help="Override experiment directory root.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show matrix entries without running",
)
def matrix(**options) -> None:
    """Run an experiment matrix from configuration."""
    from .matrix import (
        generate_matrix_entries,
    )

    scenario_paths = tuple(path.resolve() for path in options["scenario"])
    _validate_matrix_options(options, scenario_paths)
    scenario_defs = _load_matrix_scenarios(scenario_paths)
    matrix_config = _matrix_config_from_options(options)
    entries = generate_matrix_entries(matrix_config)
    experiment_config = matrix_config.experiment
    _echo_matrix_settings(matrix_config, scenario_defs, entries)

    if options["dry_run"]:
        _echo_matrix_dry_run(
            scenario_defs=scenario_defs,
            entries=entries,
            repeats=experiment_config.repeats,
        )
        return

    _cleanup_stale_harbor_before_runs()
    resolved_experiments_root = _resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    jobs = [
        (scenario_path, scenario_def, entry)
        for scenario_path, scenario_def in scenario_defs
        for entry in entries
    ]
    successes, failures = _run_matrix_jobs(
        jobs,
        experiment_config=experiment_config,
        experiments_root=resolved_experiments_root,
        experiment_kind=options["experiment_kind"],
        parallel=options["parallel"],
    )

    click.echo(f"Matrix completed: {successes} experiments succeeded, {failures} failed.")


def _validate_matrix_options(options: dict[str, object], scenario_paths: tuple[Path, ...]) -> None:
    if not scenario_paths:
        raise click.ClickException("At least one --scenario path is required.")
    if (options["config"] is None) == (options["selector"] is None):
        raise click.ClickException("Provide exactly one of --config or --selector.")


def _matrix_config_from_options(options: dict[str, object]):
    from .matrix import build_selected_matrix_config, load_matrix_config

    if options["config"] is not None:
        click.echo(f"Loading matrix from {options['config']}")
        return load_matrix_config(options["config"])
    click.echo(f"Generating matrix from selector '{options['selector']}'")
    return build_selected_matrix_config(
        selector=options["selector"],
        timeout_sec=options["timeout"],
        repeats=options["repeats"],
        repeat_parallel=options["repeat_parallel"],
        retry_void=options["rerun_unscored"],
    )


def _echo_matrix_settings(matrix_config, scenario_defs, entries) -> None:
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


def _load_matrix_scenarios(
    scenario_paths: tuple[Path, ...],
) -> list[tuple[Path, ScenarioDefinition]]:
    scenario_defs: list[tuple[Path, ScenarioDefinition]] = []
    for scenario_path in scenario_paths:
        click.echo(f"Loading scenario from {scenario_path}")
        scenario_defs.append((scenario_path, load_scenario(scenario_path)))
    return scenario_defs


def _echo_matrix_dry_run(
    *,
    scenario_defs: list[tuple[Path, ScenarioDefinition]],
    entries: list[object],
    repeats: int,
) -> None:
    for _scenario_path, scenario_def in scenario_defs:
        for entry in entries:
            reasoning_label = (
                f" [{entry.reasoning_effort}]" if getattr(entry, "reasoning_effort", None) else ""
            )
            click.echo(
                f"[dry-run] {scenario_def.name}@{scenario_def.scenario_revision}: "
                f"{entry.harness}/{entry.provider}/{entry.model}{reasoning_label} x{repeats}"
            )


def _matrix_job_options(request: dict[str, object]) -> RunCliOptions:
    entry = request["entry"]
    experiment_config = request["experiment_config"]
    return _service_build_run_cli_options(
        RunCliOptionsBuildRequest(
            scenario=request["scenario_path"],
            harness=entry.harness,
            provider=entry.provider,
            model=entry.model,
            reasoning_effort=entry.reasoning_effort,
            timeout=experiment_config.timeout_sec,
            repeats=experiment_config.repeats,
            repeat_parallel=experiment_config.repeat_parallel,
            rerun_unscored=experiment_config.retry_void,
            experiments_root=request["experiments_root"],
            experiment_kind=request["experiment_kind"],
            repo_root=REPO_ROOT,
        )
    )


def _run_matrix_jobs(
    jobs: list[tuple[Path, ScenarioDefinition, object]],
    **settings,
) -> tuple[int, int]:
    def _run_matrix_job(job: tuple[Path, ScenarioDefinition, object]) -> SuiteExecutionResult:
        scenario_path, _scenario_def, entry = job
        options = _matrix_job_options(
            {
                "scenario_path": scenario_path,
                "entry": entry,
                "experiment_config": settings["experiment_config"],
                "experiments_root": settings["experiments_root"],
                "experiment_kind": settings["experiment_kind"],
            }
        )
        return _execute_run_options(
            options,
            force_experiment_summary=True,
            cleanup_before_runs=False,
            echo=False,
            execution_suffix=_experiment_execution_suffix(options),
        )

    if settings["parallel"] > 1:
        return _run_parallel_matrix_jobs(jobs, settings["parallel"], _run_matrix_job)

    return _run_sequential_matrix_jobs(jobs, _run_matrix_job)


def _run_parallel_matrix_jobs(jobs, parallel, run_matrix_job) -> tuple[int, int]:
    successes = 0
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as executor:
        future_map = {executor.submit(run_matrix_job, job): job for job in jobs}
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


def _run_sequential_matrix_jobs(jobs, run_matrix_job) -> tuple[int, int]:
    successes = 0
    failures = 0
    for scenario_path, scenario_def, entry in jobs:
        click.echo(
            f"Running experiment: {scenario_def.name}@{scenario_def.scenario_revision} "
            f"{entry.harness}/{entry.model}"
        )
        try:
            result = run_matrix_job((scenario_path, scenario_def, entry))
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
    click.echo(f"Scenario: {scenario_def.name}")
    click.echo(f"Revision: {scenario_def.scenario_revision}")
    click.echo(f"Description: {scenario_def.description}")
    click.echo(f"Difficulty: {scenario_def.difficulty}")
    click.echo(f"Category: {scenario_def.category}")
    click.echo(f"Timeout: {scenario_def.timeout_sec // 60} minutes")
    click.echo(f"Evaluation Profile: {scenario_evaluation_profile(scenario_def)}")
    click.echo(f"Metrics: {', '.join(scenario_metrics(scenario_def))}")

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
    click.echo(
        "  Visual Pass Policy: "
        f"score>={task_def.visual.pass_policy.minimum_score}, "
        f"global>={task_def.visual.pass_policy.fail_if_global_below}, "
        f"worst_region>={task_def.visual.pass_policy.minimum_worst_region}"
    )


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
    scenario_def = load_scenario(scenario_yaml)

    _echo_scenario_summary(scenario_def)
    click.echo(f"Scenario YAML: {scenario_yaml}")
    if scenario_input.is_dir() and not (scenario_input / "scenario.yaml").is_file():
        _echo_available_revisions(scenario_input)
    _echo_rule_variants(scenario_yaml.parent)
    _echo_visual_config(scenario_def)
    _echo_acceptance_config(scenario_def)


if __name__ == "__main__":
    main()
