"""Scenario lifecycle and inspection CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from raidar.agents.config import Harness
from raidar.agents.rules import inject_rules
from raidar.application.models import ScenarioCloneRequest, ScenarioInitRequest
from raidar.application.scenario_catalog import (
    load_scenario,
    scenario_evaluation_profile,
    scenario_metrics,
    scenario_scorers,
)
from raidar.application.scenarios import clone_scenario_revision as _service_clone_scenario_revision
from raidar.application.scenarios import init_scenario as _service_init_scenario
from raidar.application.scenarios import scenario_revision_paths as _service_scenario_revision_paths
from raidar.application.scenarios import validate_scenario as _service_validate_scenario
from raidar.application.serializers import scenario_clone_payload, scenario_init_payload
from raidar.commands.shared import HARNESS_CHOICES, REPO_ROOT, resolve_scenario_yaml
from raidar.schemas.scenario import ScenarioDefinition


def register(main: click.Group) -> None:
    main.add_command(scenario)
    main.add_command(inject)
    main.add_command(info)


@click.group()
def scenario() -> None:
    """Scenario lifecycle commands."""


def scenario_revision_paths(scenario_root: Path) -> list[Path]:
    return _service_scenario_revision_paths(scenario_root)


def list_scenarios_with_revisions(scenarios_root: Path) -> list[tuple[str, tuple[str, ...]]]:
    if not scenarios_root.exists():
        return []

    scenarios: list[tuple[str, tuple[str, ...]]] = []
    for scenario_root in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
        revision_paths = scenario_revision_paths(scenario_root)
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
)
def scenario_list(scenarios_root: Path) -> None:
    """List available scenarios and their revisions."""
    for scenario_id, revisions in list_scenarios_with_revisions(scenarios_root.resolve()):
        click.echo(f"{scenario_id} | revisions: {', '.join(revisions)}")


@scenario.command("init")
@click.option("--path", "-p", type=click.Path(path_type=Path), required=True)
@click.option("--name", type=str)
@click.option("--scenario-revision", type=str, default="v001")
@click.option("--starter-root", type=str, default="starter")
@click.option("--prompt-entry", type=str, default="prompt/task.md")
@click.option("--difficulty", type=click.Choice(["easy", "medium", "hard"]), default="medium")
@click.option("--category", type=str, default="greenfield-ui")
@click.option("--timeout", type=int, default=1800)
@click.option("--json", "as_json", is_flag=True)
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
        click.echo(json.dumps(scenario_init_payload(result), indent=2))
        return
    click.echo(f"Created scenario at {result.scenario_yaml}")


@scenario.command("validate")
@click.option("--scenario", "-s", type=click.Path(exists=True, path_type=Path), required=True)
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
    click.echo(f"  parent_revision: {scenario_def.parent_revision}")
    click.echo(f"  starter_root: {scenario_def.starter.root}")
    click.echo(f"  prompt_entry: {scenario_def.prompt.entry}")
    click.echo(f"  required_commands: {len(scenario_def.verification.required_commands)}")
    click.echo(f"  gates: {len(scenario_def.verification.gates)}")
    click.echo(f"  scorers: {len(scenario_def.scorers)}")
    click.echo(f"  metrics: {len(scenario_def.metric_ids())}")


@scenario.command("clone-revision")
@click.option(
    "--path",
    "-p",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option("--from-revision", type=str, required=True)
@click.option("--to-revision", type=str)
@click.option("--json", "as_json", is_flag=True)
def scenario_clone_revision(
    path: Path, from_revision: str, to_revision: str | None, as_json: bool
) -> None:
    """Clone a scenario revision and update revision metadata."""
    try:
        result = _service_clone_scenario_revision(
            ScenarioCloneRequest(path=path, from_revision=from_revision, to_revision=to_revision)
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(scenario_clone_payload(result), indent=2))
        return
    click.echo("Scenario revision clone completed.")
    click.echo(f"  scenario_root: {result.scenario_root}")
    click.echo(f"  source_revision: {result.source_revision}")
    click.echo(f"  target_revision: {result.target_revision}")
    click.echo(f"  parent_revision: {result.parent_revision}")
    click.echo(f"  scenario_yaml: {result.target_scenario_yaml}")


@click.command()
@click.option("--scenario", "-s", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--harness", "-a", type=click.Choice(HARNESS_CHOICES), required=True)
@click.option("--starter", "-r", type=click.Path(exists=True, path_type=Path), required=True)
def inject(scenario: Path, harness: str, starter: Path) -> None:
    """Inject rules into a starter workspace for testing."""
    click.echo(f"Injecting rules for {harness}")
    rules_dir = scenario / "rules"
    result = inject_rules(rules_dir, starter, Harness(harness))
    click.echo(f"Injected: {result}")


def echo_scenario_summary(scenario_def: ScenarioDefinition) -> None:
    click.echo(f"Scenario: {scenario_def.name}")
    click.echo(f"Revision: {scenario_def.scenario_revision}")
    click.echo(f"Parent Revision: {scenario_def.parent_revision}")
    click.echo(f"Description: {scenario_def.description}")
    click.echo(f"Difficulty: {scenario_def.difficulty}")
    click.echo(f"Category: {scenario_def.category}")
    click.echo(f"Timeout: {scenario_def.timeout_sec // 60} minutes")
    click.echo(f"Evaluation Profile: {scenario_evaluation_profile(scenario_def)}")
    click.echo(f"Scorers: {', '.join(scenario_scorers(scenario_def))}")
    click.echo(f"Metrics: {', '.join(scenario_metrics(scenario_def))}")
    if scenario_def.verification.gates:
        gates = [g.name for g in scenario_def.verification.gates]
        click.echo(f"Quality Gates: {', '.join(gates)}")


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
    files = sorted(f.name for f in rules_dir.iterdir() if f.is_file())
    click.echo(f"  files: {', '.join(files) if files else '(none)'}")


def echo_visual_config(task_def: ScenarioDefinition) -> None:
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


def echo_requirements_config(task_def: ScenarioDefinition) -> None:
    if not task_def.requirements.items:
        return
    click.echo()
    click.echo("Requirements Config:")
    click.echo(f"  Requirements: {len(task_def.requirements.items)}")


@click.command()
@click.option("--scenario", "-s", type=click.Path(exists=True, path_type=Path), required=True)
def info(scenario: Path) -> None:
    """Show scenario information and details."""
    scenario_input = scenario.resolve()
    scenario_yaml = resolve_scenario_yaml(scenario_input)
    scenario_def = load_scenario(scenario_yaml)

    echo_scenario_summary(scenario_def)
    click.echo(f"Scenario YAML: {scenario_yaml}")
    if scenario_input.is_dir() and not (scenario_input / "scenario.yaml").is_file():
        echo_available_revisions(scenario_input)
    echo_rule_variants(scenario_yaml.parent)
    echo_visual_config(scenario_def)
    echo_requirements_config(scenario_def)
