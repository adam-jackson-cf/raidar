"""Scenario validation command."""

from __future__ import annotations

from pathlib import Path

import click

from raidar.application.scenarios import validate_scenario
from raidar.commands.shared import REPO_ROOT
from raidar.runtime.environments import (
    EnvironmentResolutionError,
    resolve_environment_registry,
    resolve_scenario_environment,
)
from raidar.runtime.profile import default_runtime_profile


@click.command("validate")
@click.option("--scenario", "-s", type=click.Path(exists=True, path_type=Path), required=True)
def scenario_validate(scenario: Path) -> None:
    """Validate a scenario document and report key configuration fields."""

    try:
        result = validate_scenario(scenario)
    except (FileNotFoundError, EnvironmentResolutionError) as exc:
        raise click.ClickException(str(exc)) from exc
    scenario_def = result.scenario
    runtime_profile = default_runtime_profile()
    environment = resolve_scenario_environment(
        scenario=scenario_def,
        scenario_path=result.scenario_path,
        repo_root=REPO_ROOT,
        registry=resolve_environment_registry(
            repo_root=REPO_ROOT,
            runtime_profile=runtime_profile,
        ),
        docker_registry_allowlist=runtime_profile.docker_registry_allowlist,
    )
    click.echo("Scenario validation passed.")
    click.echo(f"  name: {scenario_def.name}")
    click.echo(f"  scenario_revision: {scenario_def.scenario_revision}")
    click.echo(f"  parent_revision: {scenario_def.parent_revision}")
    click.echo(f"  starter_root: {scenario_def.starter.root}")
    click.echo(f"  prompt_entry: {scenario_def.prompt.entry}")
    click.echo(f"  required_commands: {len(scenario_def.verification.required_commands)}")
    click.echo(f"  gates: {len(scenario_def.verification.gates)}")
    click.echo(f"  scorers: {len(scenario_def.scorers)}")
    click.echo(f"  metrics: {len(scenario_def.metric_ids())}")
    click.echo(f"  environment: {environment.id}")
    click.echo(f"  image: {environment.image}")
