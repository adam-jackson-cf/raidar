"""Scenario lifecycle and inspection CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from raidar.agents.config import Harness
from raidar.agents.rules import inject_rules
from raidar.application.models import (
    CATALOG_STATUS_CHOICES,
    DELIVERY_MODE_CHOICES,
    DIFFICULTY_CHOICES,
    EVALUATION_ROLE_CHOICES,
    SCENARIO_TEMPLATE_CHOICES,
    STARTING_CONDITION_CHOICES,
    TASK_KIND_CHOICES,
    WORKSPACE_STATE_CHOICES,
    ScenarioCloneRequest,
    ScenarioDefinition,
    ScenarioInitRequest,
)
from raidar.application.scenario_catalog import (
    load_scenario,
)
from raidar.application.scenarios import clone_scenario_revision as _service_clone_scenario_revision
from raidar.application.scenarios import init_scenario as _service_init_scenario
from raidar.application.scenarios import scenario_revision_paths as _service_scenario_revision_paths
from raidar.application.serializers import scenario_clone_payload, scenario_init_payload
from raidar.commands.scenario_inspection import info
from raidar.commands.scenario_validation import scenario_validate
from raidar.commands.shared import HARNESS_CHOICES, REPO_ROOT


def register(main: click.Group) -> None:
    main.add_command(scenario)
    main.add_command(inject)
    main.add_command(info)


@click.group()
def scenario() -> None:
    """Scenario lifecycle commands."""


scenario.add_command(scenario_validate)


SCENARIO_EPOCH_ARCHIVED_OVERRIDE_ENV = "RAIDAR_ALLOW_ARCHIVED_SCENARIO_AUTHORING"
SCENARIO_EPOCH_ARCHIVED_MESSAGE = (
    "Scenario epoch is archived: scenarios/, matrices/ and experiments/ are read-only "
    "history and no new scenarios or scenario revisions are accepted. "
    "See docs/reboot/requirements.md and docs/reboot/implementation-plan.md."
)


def _refuse_archived_scenario_authoring(command: str) -> None:
    """Refuse scenario authoring now that the scenario epoch is archived.

    The override env var exists only so tests can keep exercising the archived
    authoring code paths; it is not a supported public workflow.
    """
    if os.environ.get(SCENARIO_EPOCH_ARCHIVED_OVERRIDE_ENV) == "1":
        return
    raise click.ClickException(f"Refusing scenario {command}: {SCENARIO_EPOCH_ARCHIVED_MESSAGE}")


def scenario_revision_paths(scenario_root: Path) -> list[Path]:
    return _service_scenario_revision_paths(scenario_root)


def _parse_variant_axes(
    _ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]
) -> dict[str, str]:
    axes: dict[str, str] = {}
    for value in values:
        key, separator, axis_value = value.partition("=")
        if not separator or not key.strip() or not axis_value.strip():
            raise click.BadParameter("must use KEY=VALUE")
        if key in axes:
            raise click.BadParameter(f"duplicate variant axis: {key}")
        axes[key] = axis_value
    return axes


def _format_scenario_taxonomy(scenario_def) -> str:
    starting_conditions = ", ".join(scenario_def.starting_conditions) or "none"
    languages = ", ".join(scenario_def.languages)
    variant_axes = (
        ", ".join(f"{key}={value}" for key, value in sorted(scenario_def.variant_axes.items()))
        or "none"
    )
    return (
        f"display_name: {scenario_def.display_name} | "
        f"task_kind: {scenario_def.task_kind} | "
        f"delivery_mode: {scenario_def.delivery_mode} | "
        f"workspace_state: {scenario_def.workspace_state} | "
        f"starting_conditions: {starting_conditions} | "
        f"languages: {languages} | "
        f"evaluation_role: {scenario_def.evaluation_role} | "
        f"catalog_status: {scenario_def.catalog_status} | "
        f"family_id: {scenario_def.family_id or 'none'} | "
        f"variant_axes: {variant_axes}"
    )


def list_scenarios_with_revisions(
    scenarios_root: Path,
) -> list[tuple[ScenarioDefinition, tuple[str, ...]]]:
    if not scenarios_root.exists():
        return []

    scenarios: list[tuple[ScenarioDefinition, tuple[str, ...]]] = []
    for scenario_root in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
        revision_paths = scenario_revision_paths(scenario_root)
        if not revision_paths:
            continue
        scenario_def = load_scenario(revision_paths[-1])
        revisions = tuple(path.parent.name for path in revision_paths)
        scenarios.append((scenario_def, revisions))
    return sorted(scenarios, key=lambda entry: entry[0].name)


@scenario.command("list")
@click.option(
    "--scenarios-root",
    type=click.Path(path_type=Path),
    default=REPO_ROOT / "scenarios",
    show_default=True,
)
def scenario_list(scenarios_root: Path) -> None:
    """List available scenarios and their revisions."""
    for scenario_def, revisions in list_scenarios_with_revisions(scenarios_root.resolve()):
        click.echo(
            f"{scenario_def.name} | {_format_scenario_taxonomy(scenario_def)} | "
            f"revisions: {', '.join(revisions)}"
        )


@scenario.command("init")
@click.option("--path", "-p", type=click.Path(path_type=Path), required=True)
@click.option("--name", type=str)
@click.option("--display-name", type=str, required=True)
@click.option("--scenario-revision", type=str, default="v001")
@click.option("--starter-root", type=str, default="starter")
@click.option("--prompt-entry", type=str, default="prompt/task.md")
@click.option("--difficulty", type=click.Choice(DIFFICULTY_CHOICES), default="medium")
@click.option(
    "--task-kind",
    type=click.Choice(TASK_KIND_CHOICES),
    required=True,
)
@click.option(
    "--delivery-mode",
    type=click.Choice(DELIVERY_MODE_CHOICES),
    required=True,
)
@click.option("--workspace-state", type=click.Choice(WORKSPACE_STATE_CHOICES), required=True)
@click.option(
    "--starting-condition",
    "starting_conditions",
    type=click.Choice(STARTING_CONDITION_CHOICES),
    multiple=True,
)
@click.option("--language", "languages", type=str, multiple=True, required=True)
@click.option("--evaluation-role", type=click.Choice(EVALUATION_ROLE_CHOICES), required=True)
@click.option(
    "--catalog-status",
    type=click.Choice(CATALOG_STATUS_CHOICES),
    required=True,
)
@click.option("--family-id", type=str)
@click.option(
    "--variant-axis",
    "variant_axes",
    type=str,
    multiple=True,
    callback=_parse_variant_axes,
)
@click.option("--timeout", type=int, default=1800)
@click.option(
    "--template",
    type=click.Choice(SCENARIO_TEMPLATE_CHOICES),
    required=True,
)
@click.option("--json", "as_json", is_flag=True)
def scenario_init(**options) -> None:
    """Create a new versioned scenario descriptor (archived: refuses by default)."""
    _refuse_archived_scenario_authoring("init")
    try:
        result = _service_init_scenario(
            ScenarioInitRequest(
                path=options["path"],
                name=options["name"],
                display_name=options["display_name"],
                scenario_revision=options["scenario_revision"],
                starter_root=options["starter_root"],
                prompt_entry=options["prompt_entry"],
                difficulty=options["difficulty"],
                task_kind=options["task_kind"],
                delivery_mode=options["delivery_mode"],
                workspace_state=options["workspace_state"],
                starting_conditions=list(options["starting_conditions"]),
                languages=list(options["languages"]),
                evaluation_role=options["evaluation_role"],
                catalog_status=options["catalog_status"],
                timeout_sec=options["timeout"],
                template=options["template"],
                family_id=options["family_id"],
                variant_axes=options["variant_axes"],
            )
        )
    except (FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if options["as_json"]:
        click.echo(json.dumps(scenario_init_payload(result), indent=2))
        return
    click.echo(f"Created scenario at {result.scenario_yaml}")


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
    """Clone a scenario revision (archived: refuses by default)."""
    _refuse_archived_scenario_authoring("clone-revision")
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
