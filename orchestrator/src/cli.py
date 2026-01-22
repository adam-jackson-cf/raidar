"""CLI entrypoint for eval orchestrator."""

import json
from pathlib import Path
from typing import Literal

import click

from .harness.config import Agent, HarnessConfig, ModelConfig
from .runner import load_task, prepare_workspace, run_task


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """Eval orchestrator for testing model/harness combinations."""
    pass


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
    type=click.Choice(["claude-code", "codex", "gemini", "openhands"]),
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
    "--rules",
    "-r",
    type=click.Choice(["strict", "minimal", "none"]),
    default="strict",
    help="Rules variant to inject",
)
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    default=Path("scaffold"),
    help="Path to scaffold template",
)
@click.option(
    "--workspace",
    "-w",
    type=click.Path(path_type=Path),
    default=Path("workspace"),
    help="Path to create workspace",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("results"),
    help="Path to store results",
)
@click.option(
    "--timeout",
    type=int,
    default=1800,
    help="Task timeout in seconds",
)
def run(
    task: Path,
    agent: str,
    model: str,
    rules: Literal["strict", "minimal", "none"],
    scaffold: Path,
    workspace: Path,
    output: Path,
    timeout: int,
) -> None:
    """Run a task with specified harness and model."""
    click.echo(f"Loading task from {task}")
    task_def = load_task(task)

    click.echo(f"Task: {task_def.name}")
    click.echo(f"Agent: {agent}")
    click.echo(f"Model: {model}")
    click.echo(f"Rules variant: {rules}")

    config = HarnessConfig(
        agent=Agent(agent),
        model=ModelConfig.from_string(model),
        rules_variant=rules,
        timeout_sec=timeout,
    )

    output.mkdir(parents=True, exist_ok=True)

    click.echo("Running task...")
    result = run_task(
        task=task_def,
        config=config,
        scaffold_dir=scaffold,
        task_dir=task.parent,
        workspace_dir=workspace,
        results_dir=output,
    )

    # Save result
    result_path = output / f"{result.id}.json"
    with open(result_path, "w") as f:
        f.write(result.model_dump_json(indent=2))

    click.echo(f"Result saved to {result_path}")
    click.echo(f"Run ID: {result.id}")
    click.echo(f"Duration: {result.duration_sec:.1f}s")
    click.echo(f"Terminated early: {result.terminated_early}")
    if result.termination_reason:
        click.echo(f"Reason: {result.termination_reason}")


@main.command()
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scaffold directory",
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
    type=click.Choice(["claude-code", "codex", "gemini", "copilot"]),
    required=True,
    help="Agent to inject rules for",
)
@click.option(
    "--rules",
    "-r",
    type=click.Choice(["strict", "minimal", "none"]),
    default="strict",
    help="Rules variant to inject",
)
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to scaffold directory",
)
def inject(
    task: Path,
    agent: str,
    rules: Literal["strict", "minimal", "none"],
    scaffold: Path,
) -> None:
    """Inject rules into scaffold for testing."""
    from .harness.rules import inject_rules

    click.echo(f"Injecting {rules} rules for {agent}")
    rules_dir = task / "rules"
    result = inject_rules(rules_dir, scaffold, agent, rules)
    if result:
        click.echo(f"Injected: {result}")
    else:
        click.echo("No rules injected")


@main.command()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to matrix configuration YAML",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show matrix entries without running",
)
def matrix(config: Path, dry_run: bool) -> None:
    """Run evaluation matrix from configuration."""
    from .matrix import generate_matrix_entries, load_matrix_config

    click.echo(f"Loading matrix from {config}")
    matrix_config = load_matrix_config(config)
    entries = generate_matrix_entries(matrix_config)

    click.echo(f"Generated {len(entries)} matrix entries:")
    for i, entry in enumerate(entries, 1):
        click.echo(f"  {i}. {entry.harness} + {entry.model} + {entry.rules_variant}")

    if dry_run:
        click.echo("\nDry run - no tasks executed")
        return

    click.echo("\nNote: Full matrix execution requires Harbor. Run entries individually with 'run' command.")


@main.command()
@click.option(
    "--results",
    "-r",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to results directory",
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
    """Generate comparison report from results."""
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


if __name__ == "__main__":
    main()
