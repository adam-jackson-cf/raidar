"""Scenario inspection command rendering."""

from __future__ import annotations

import json
from pathlib import Path

import click

from raidar.application.scenario_catalog import (
    load_scenario,
    scenario_evaluation_profile,
    scenario_metrics,
    scenario_scorers,
)
from raidar.application.scenarios import scenario_revision_paths
from raidar.commands.shared import REPO_ROOT, resolve_scenario_yaml
from raidar.runtime.environments import resolve_environment_registry, resolve_scenario_environment
from raidar.runtime.profile import default_runtime_profile
from raidar.schemas.scenario import ScenarioDefinition


def echo_scenario_summary(scenario_def: ScenarioDefinition) -> None:
    click.echo(f"Scenario: {scenario_def.name}")
    click.echo(f"Display Name: {scenario_def.display_name}")
    click.echo(f"Revision: {scenario_def.scenario_revision}")
    click.echo(f"Parent Revision: {scenario_def.parent_revision}")
    click.echo(f"Description: {scenario_def.description}")
    click.echo(f"Difficulty: {scenario_def.difficulty}")
    click.echo(f"Task Kind: {scenario_def.task_kind}")
    click.echo(f"Delivery Mode: {scenario_def.delivery_mode}")
    click.echo(f"Workspace State: {scenario_def.workspace_state}")
    click.echo(f"Starting Conditions: {', '.join(scenario_def.starting_conditions) or 'none'}")
    click.echo(f"Languages: {', '.join(scenario_def.languages)}")
    click.echo(f"Evaluation Role: {scenario_def.evaluation_role}")
    click.echo(f"Catalog Status: {scenario_def.catalog_status}")
    click.echo(f"Family ID: {scenario_def.family_id or 'none'}")
    click.echo(f"Variant Axes: {json.dumps(scenario_def.variant_axes, sort_keys=True)}")
    click.echo(f"Timeout: {scenario_def.timeout_sec // 60} minutes")
    click.echo(f"Evaluation Profile: {scenario_evaluation_profile(scenario_def)}")
    click.echo(f"Scorers: {', '.join(scenario_scorers(scenario_def))}")
    click.echo(f"Metrics: {', '.join(scenario_metrics(scenario_def))}")
    if scenario_def.verification.gates:
        gates = [gate.name for gate in scenario_def.verification.gates]
        click.echo(f"Quality Gates: {', '.join(gates)}")


def echo_environment_config(scenario_yaml: Path, scenario_def: ScenarioDefinition) -> None:
    runtime_profile = default_runtime_profile()
    environment = resolve_scenario_environment(
        scenario=scenario_def,
        scenario_path=scenario_yaml,
        repo_root=REPO_ROOT,
        registry=resolve_environment_registry(
            repo_root=REPO_ROOT,
            runtime_profile=runtime_profile,
        ),
        docker_registry_allowlist=runtime_profile.docker_registry_allowlist,
    )
    click.echo()
    click.echo("Environment:")
    click.echo(f"  ID: {environment.id}")
    click.echo(f"  Image: {environment.image}")
    click.echo(f"  Workdir: {environment.workdir}")
    click.echo(f"  Dockerfile: {environment.dockerfile_path}")
    requirements = environment.requirements.model_dump(mode="json")
    capabilities = environment.library.capabilities.model_dump(mode="json")
    click.echo(f"  Requirements: {json.dumps(requirements, sort_keys=True)}")
    click.echo(f"  Capabilities: {json.dumps(capabilities, sort_keys=True)}")


def echo_available_revisions(scenario_root: Path) -> None:
    revision_paths = scenario_revision_paths(scenario_root)
    if not revision_paths:
        return
    click.echo("Available Revisions:")
    for scenario_yaml in revision_paths:
        click.echo(f"  {scenario_yaml.parent.name}: {scenario_yaml.resolve()}")


def echo_rule_variants(scenario_dir: Path) -> None:
    rules_dir = scenario_dir / "rules"
    if not rules_dir.exists():
        return
    click.echo()
    click.echo("Rules:")
    files = sorted(path.name for path in rules_dir.iterdir() if path.is_file())
    click.echo(f"  files: {', '.join(files) if files else '(none)'}")


def echo_visual_config(scenario_def: ScenarioDefinition) -> None:
    if not scenario_def.visual:
        return
    click.echo()
    click.echo("Visual Config:")
    click.echo(f"  Reference: {scenario_def.visual.reference_image}")
    click.echo(
        "  Visual Pass Policy: "
        f"score>={scenario_def.visual.pass_policy.minimum_score}, "
        f"global>={scenario_def.visual.pass_policy.fail_if_global_below}, "
        f"worst_region>={scenario_def.visual.pass_policy.minimum_worst_region}"
    )


def echo_requirements_config(scenario_def: ScenarioDefinition) -> None:
    if not scenario_def.requirements.items:
        return
    click.echo()
    click.echo("Requirements Config:")
    click.echo(f"  Requirements: {len(scenario_def.requirements.items)}")


@click.command()
@click.option("--scenario", "-s", type=click.Path(exists=True, path_type=Path), required=True)
def info(scenario: Path) -> None:
    """Show scenario information and details."""
    scenario_input = scenario.resolve()
    scenario_yaml = resolve_scenario_yaml(scenario_input)
    scenario_def = load_scenario(scenario_yaml)

    echo_scenario_summary(scenario_def)
    click.echo(f"Scenario YAML: {scenario_yaml}")
    echo_environment_config(scenario_yaml, scenario_def)
    if scenario_input.is_dir() and not (scenario_input / "scenario.yaml").is_file():
        echo_available_revisions(scenario_input)
    echo_rule_variants(scenario_yaml.parent)
    echo_visual_config(scenario_def)
    echo_requirements_config(scenario_def)
