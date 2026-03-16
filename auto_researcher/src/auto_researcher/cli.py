"""CLI surface for objective-to-scenario autoresearch workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

import click

from .engine import AutoResearchEngine
from .models import DEFAULT_MUTATION_SURFACE, ObjectiveInitRequest, RoleModelConfig
from .pi_rpc import PiRpcRoleRunner
from .raidar_cli import RaidarCli
from .storage import WorkspaceLayout

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_engine(pi_binary: str) -> AutoResearchEngine:
    layout = WorkspaceLayout(REPO_ROOT)
    return AutoResearchEngine(
        layout=layout,
        role_runner=PiRpcRoleRunner(layout=layout, pi_binary=pi_binary),
        raidar=RaidarCli(layout=layout),
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


@click.group()
def main() -> None:
    """PI-driven objective-to-scenario autoresearch."""


@main.command("init")
@click.option("--goal", required=True, type=str, help="Optimization objective.")
@click.option("--target-harness", required=True, type=str, help="Harness under evaluation.")
@click.option("--target-model", required=True, type=str, help="Model under evaluation.")
@click.option("--objective-id", type=str, help="Stable objective identifier.")
@click.option("--approval-mode", default="scenario_only", show_default=True, type=str)
@click.option("--loop-topology", default="bounded_parallel", show_default=True, type=str)
@click.option("--max-revisions", default=3, show_default=True, type=int)
@click.option("--max-parallel-loops", default=3, show_default=True, type=int)
@click.option("--benchmark-repeats", default=5, show_default=True, type=int)
@click.option("--research-repeats", default=3, show_default=True, type=int)
@click.option(
    "--mutation-surface",
    multiple=True,
    default=tuple(DEFAULT_MUTATION_SURFACE),
    show_default=True,
    type=str,
)
@click.option("--control-provider", default="openai-codex", show_default=True, type=str)
@click.option("--control-model", default="gpt-5.4", show_default=True, type=str)
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
    loop_topology: str,
    max_revisions: int,
    max_parallel_loops: int,
    benchmark_repeats: int,
    research_repeats: int,
    mutation_surface: tuple[str, ...],
    control_provider: str,
    control_model: str,
    role_models: tuple[str, ...],
    pi_binary: str,
) -> None:
    engine = build_engine(pi_binary)
    request = ObjectiveInitRequest(
        goal=goal,
        target_harness=target_harness,
        target_model=target_model,
        objective_id=objective_id,
        approval_mode=cast(Literal["scenario_only"], approval_mode),
        loop_topology=cast(Literal["bounded_parallel"], loop_topology),
        max_revisions=max_revisions,
        max_parallel_loops=max_parallel_loops,
        benchmark_repeats=benchmark_repeats,
        research_repeats=research_repeats,
        mutation_surface=list(mutation_surface),
        role_models=_parse_role_models(control_provider, control_model, role_models),
    )
    try:
        objective = engine.init_objective(request)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"objective_id={objective.objective_id}")
    click.echo(f"status={objective.status}")
    click.echo(f"draft_scenario_ref={objective.draft_scenario_ref}")


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
