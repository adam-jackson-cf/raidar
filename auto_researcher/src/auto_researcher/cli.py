"""CLI surface for objective-to-scenario autoresearch workflows."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import click

from .engine import AutoResearchEngine
from .models import (
    DEFAULT_MUTATION_SURFACE,
    ObjectiveInitRequest,
    RoleModelConfig,
)
from .pi_rpc import PiRpcRoleRunner
from .raidar_cli import RaidarServiceClient
from .scripted import (
    ScriptedRaidar,
    ScriptedRoleRunner,
    load_objective_fixture,
    load_script_fixture,
)
from .storage import WorkspaceLayout, ensure_dir

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DEMO_OBJECTIVE_FIXTURE = REPO_ROOT / "auto_researcher" / "examples" / "demo-objective.yaml"
DEFAULT_DEMO_SCRIPT_FIXTURE = REPO_ROOT / "auto_researcher" / "examples" / "demo-script.json"


def build_engine(pi_binary: str) -> AutoResearchEngine:
    layout = WorkspaceLayout(REPO_ROOT)
    return AutoResearchEngine(
        layout=layout,
        role_runner=PiRpcRoleRunner(layout=layout, pi_binary=pi_binary),
        raidar=RaidarServiceClient(layout=layout),
    )


def build_scripted_engine(workspace_root: Path, script_fixture: Path) -> AutoResearchEngine:
    layout = WorkspaceLayout(workspace_root)
    for root in (
        layout.auto_researcher_root,
        layout.objectives_root,
        layout.scenarios_root,
        layout.benchmark_experiments_root,
        layout.research_loop_experiments_root,
    ):
        ensure_dir(root)
    role_scripts, experiment_payloads = load_script_fixture(script_fixture)
    return AutoResearchEngine(
        layout=layout,
        role_runner=ScriptedRoleRunner(layout=layout, scripts=role_scripts),
        raidar=ScriptedRaidar(layout=layout, experiment_payloads=experiment_payloads),
    )


def _parse_role_models(
    control_provider: str,
    control_model: str,
    overrides: tuple[str, ...],
) -> dict[str, RoleModelConfig]:
    role_models: dict[str, RoleModelConfig] = {
        "__default__": RoleModelConfig(provider=control_provider, model_id=control_model)
    }
    for raw in overrides:
        if "=" not in raw or "/" not in raw:
            raise click.ClickException("Role model overrides must use ROLE=provider/model format.")
        role, value = raw.split("=", 1)
        provider, model_id = value.split("/", 1)
        role_models[role.strip()] = RoleModelConfig(
            provider=provider.strip(),
            model_id=model_id.strip(),
        )
    return role_models


@dataclass(frozen=True, slots=True)
class InitCommandConfig:
    """Typed CLI request for the autoresearch init command."""

    engine: AutoResearchEngine
    objective_request: ObjectiveInitRequest


def _dispatch_init_command(config: InitCommandConfig) -> None:
    """Run the init command through one typed dispatch helper."""

    try:
        objective = config.engine.init_objective(config.objective_request)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"objective_id={objective.objective_id}")
    click.echo(f"status={objective.status}")
    click.echo(f"draft_scenario_ref={objective.draft_scenario_ref}")


@click.group()
def main() -> None:
    """PI-driven objective-to-scenario autoresearch."""


@main.command("init")
@click.option("--goal", required=True, type=str, help="Optimization objective.")
@click.option("--target-harness", required=True, type=str, help="Harness under evaluation.")
@click.option("--target-model", required=True, type=str, help="Model under evaluation.")
@click.option("--objective-id", type=str, help="Stable objective identifier.")
@click.option(
    "--approval-mode",
    default="scenario_only",
    show_default=True,
    type=click.Choice(["scenario_only"]),
)
@click.option(
    "--loop-execution-mode",
    default="serial",
    show_default=True,
    type=click.Choice(["serial", "parallel"]),
)
@click.option("--max-revisions", default=3, show_default=True, type=int)
@click.option("--max-parallel-loops", default=3, show_default=True, type=int)
@click.option("--benchmark-repeats", default=5, show_default=True, type=int)
@click.option("--benchmark-repeat-parallel", default=1, show_default=True, type=click.IntRange(1))
@click.option("--research-repeats", default=3, show_default=True, type=int)
@click.option("--research-repeat-parallel", default=1, show_default=True, type=click.IntRange(1))
@click.option(
    "--mutation-surface",
    multiple=True,
    default=tuple(DEFAULT_MUTATION_SURFACE),
    show_default=True,
    type=str,
)
@click.option("--control-provider", default="openai-codex", show_default=True, type=str)
@click.option("--control-model", default="gpt-5.3-codex", show_default=True, type=str)
@click.option(
    "--role-model",
    "role_models",
    multiple=True,
    help="Override one role model as ROLE=provider/model.",
)
@click.option("--pi-binary", default="pi", show_default=True, type=str)
def init_command(
    goal: str,
    target_harness: str,
    target_model: str,
    objective_id: str | None,
    approval_mode: str,
    loop_execution_mode: str,
    max_revisions: int,
    max_parallel_loops: int,
    benchmark_repeats: int,
    benchmark_repeat_parallel: int,
    research_repeats: int,
    research_repeat_parallel: int,
    mutation_surface: tuple[str, ...],
    control_provider: str,
    control_model: str,
    role_models: tuple[str, ...],
    pi_binary: str,
) -> None:
    _dispatch_init_command(
        InitCommandConfig(
            engine=build_engine(pi_binary),
            objective_request=ObjectiveInitRequest(
                goal=goal,
                target_harness=target_harness,
                target_model=target_model,
                objective_id=objective_id,
                approval_mode=cast(Literal["scenario_only"], approval_mode),
                loop_execution_mode=cast(Literal["serial", "parallel"], loop_execution_mode),
                max_revisions=max_revisions,
                max_parallel_loops=max_parallel_loops,
                benchmark_repeats=benchmark_repeats,
                benchmark_repeat_parallel=benchmark_repeat_parallel,
                research_repeats=research_repeats,
                research_repeat_parallel=research_repeat_parallel,
                mutation_surface=list(mutation_surface),
                role_models=_parse_role_models(control_provider, control_model, role_models),
            ),
        )
    )


@main.command("approve-scenario")
@click.option("--objective-id", required=True, type=str)
@click.option("--pi-binary", default="pi", show_default=True, type=str)
def approve_scenario_command(objective_id: str, pi_binary: str) -> None:
    engine = build_engine(pi_binary)
    try:
        objective = engine.approve_scenario(objective_id)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"objective_id={objective.objective_id}")
    click.echo(f"status={objective.status}")
    click.echo(f"scenario_ref={objective.scenario_ref}")
    click.echo(f"best_benchmark_ref={objective.best_benchmark_ref}")


@main.command("run")
@click.option("--objective-id", required=True, type=str)
@click.option("--pi-binary", default="pi", show_default=True, type=str)
def run_command(objective_id: str, pi_binary: str) -> None:
    engine = build_engine(pi_binary)
    try:
        objective = engine.run_objective(objective_id)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"objective_id={objective.objective_id}")
    click.echo(f"status={objective.status}")
    click.echo(f"best_benchmark_ref={objective.best_benchmark_ref}")
    click.echo(f"scenario_ref={objective.scenario_ref}")


@main.command("status")
@click.option("--objective-id", required=True, type=str)
@click.option("--pi-binary", default="pi", show_default=True, type=str)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def status_command(objective_id: str, pi_binary: str, as_json: bool) -> None:
    engine = build_engine(pi_binary)
    try:
        payload = engine.objective_status(objective_id)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    for key, value in payload.items():
        click.echo(f"{key}={value}")


@main.command("report")
@click.option("--objective-id", required=True, type=str)
@click.option("--pi-binary", default="pi", show_default=True, type=str)
def report_command(objective_id: str, pi_binary: str) -> None:
    engine = build_engine(pi_binary)
    try:
        click.echo(engine.render_objective_report(objective_id))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("demo-smoke")
@click.option(
    "--objective-fixture",
    default=str(DEFAULT_DEMO_OBJECTIVE_FIXTURE),
    show_default=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--script-fixture",
    default=str(DEFAULT_DEMO_SCRIPT_FIXTURE),
    show_default=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    help="Optional workspace root for the scripted smoke run.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def demo_smoke_command(
    objective_fixture: Path,
    script_fixture: Path,
    workspace: Path | None,
    as_json: bool,
) -> None:
    workspace_root = workspace or Path(tempfile.mkdtemp(prefix="raidar-auto-research-demo-"))
    engine = build_scripted_engine(workspace_root, script_fixture)
    request = load_objective_fixture(objective_fixture)

    try:
        created = engine.init_objective(request)
        approved = engine.approve_scenario(created.objective_id)
        completed = engine.run_objective(created.objective_id)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {
        "workspace": str(workspace_root),
        "objective_id": completed.objective_id,
        "draft_scenario_ref": created.draft_scenario_ref,
        "scenario_ref": approved.scenario_ref,
        "best_benchmark_ref": completed.best_benchmark_ref,
        "report_path": str(engine.layout.objective_report_path(completed.objective_id)),
        "status": completed.status,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    for key, value in payload.items():
        click.echo(f"{key}={value}")
