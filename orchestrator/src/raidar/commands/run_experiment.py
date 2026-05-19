"""Run and experiment CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from raidar.commands.shared import (
    EXPERIMENT_KIND_CHOICES,
    HARNESS_CHOICES,
    build_run_options_from_mapping,
    execute_run_options,
    experiment_execution_suffix,
    suite_execution_payload,
)


def register(main: click.Group) -> None:
    main.add_command(run)
    main.add_command(experiment)


@click.command()
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
@click.option("--model", "-m", type=str, required=True, help="Model identifier within provider")
@click.option("--provider", "-p", type=str, required=True, help="Upstream model provider.")
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
    help="Optional normalized reasoning/thinking effort for supported models.",
)
@click.option("--timeout", type=int, default=1800, help="Scenario timeout in seconds")
@click.option("--repeats", type=click.IntRange(min=1), default=1)
@click.option("--repeat-parallel", type=click.IntRange(min=1), default=1)
@click.option("--rerun-unscored", type=click.IntRange(min=0, max=1), default=0)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
)
@click.option("--experiments-root", type=click.Path(path_type=Path))
def run(**options) -> None:
    """Run one scenario with the specified harness and model for smoke/debug workflows."""
    run_options = build_run_options_from_mapping(options)
    execute_run_options(
        run_options,
        force_experiment_summary=False,
        cleanup_before_runs=True,
        echo=True,
    )


@click.group()
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
@click.option("--model", "-m", type=str, required=True, help="Model identifier within provider.")
@click.option("--provider", "-p", type=str, required=True, help="Upstream model provider.")
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
    help="Optional normalized reasoning/thinking effort for supported models.",
)
@click.option("--timeout", type=int, default=300, help="Scenario timeout in seconds")
@click.option("--repeats", type=click.IntRange(min=1), default=5)
@click.option("--repeat-parallel", type=click.IntRange(min=1), default=1)
@click.option("--rerun-unscored", type=click.IntRange(min=0, max=1), default=1)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
)
@click.option("--experiments-root", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output.")
def experiment_run(**options) -> None:
    """Run a repeated experiment with deterministic aggregate output."""
    run_options = build_run_options_from_mapping(options)
    result = execute_run_options(
        run_options,
        force_experiment_summary=True,
        cleanup_before_runs=True,
        echo=not options["as_json"],
        execution_suffix=experiment_execution_suffix(run_options),
    )
    if options["as_json"]:
        click.echo(json.dumps(suite_execution_payload(result), indent=2))
