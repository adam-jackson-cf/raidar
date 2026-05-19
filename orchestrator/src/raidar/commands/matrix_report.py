"""Matrix execution and report CLI commands."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import click

from raidar.application.execution import build_run_cli_options_from_request
from raidar.application.models import RunCliOptions, RunCliOptionsBuildRequest, SuiteExecutionResult
from raidar.application.scenario_catalog import load_scenario
from raidar.commands.shared import (
    EXPERIMENT_KIND_CHOICES,
    REPO_ROOT,
    cleanup_stale_harbor_before_runs,
    execute_run_options,
    experiment_execution_suffix,
    resolve_experiments_root,
)
from raidar.schemas.scenario import ScenarioDefinition


def register(main: click.Group) -> None:
    main.add_command(matrix)
    main.add_command(report)
    main.add_command(init_matrix)


@click.command()
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    multiple=True,
)
@click.option("--config", "-c", type=click.Path(exists=True, path_type=Path))
@click.option("--selector", type=click.Choice(["all", "codex", "gemini", "claude"]))
@click.option("--timeout", type=click.IntRange(min=1), default=1800, show_default=True)
@click.option("--repeats", type=click.IntRange(min=1), default=5, show_default=True)
@click.option("--repeat-parallel", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("--rerun-unscored", type=click.IntRange(min=0, max=1), default=0, show_default=True)
@click.option("--parallel", type=int, default=1)
@click.option(
    "--experiment-kind",
    type=click.Choice(EXPERIMENT_KIND_CHOICES),
    default="benchmark",
    show_default=True,
)
@click.option("--experiments-root", type=click.Path(path_type=Path))
@click.option("--dry-run", is_flag=True)
def matrix(**options) -> None:
    """Run an experiment matrix from configuration."""
    from raidar.matrix import generate_matrix_entries

    scenario_paths = tuple(path.resolve() for path in options["scenario"])
    validate_matrix_options(options, scenario_paths)
    scenario_defs = load_matrix_scenarios(scenario_paths)
    matrix_config = matrix_config_from_options(options)
    entries = generate_matrix_entries(matrix_config)
    experiment_config = matrix_config.experiment
    echo_matrix_settings(matrix_config, scenario_defs, entries)

    if options["dry_run"]:
        echo_matrix_dry_run(
            scenario_defs=scenario_defs,
            entries=entries,
            repeats=experiment_config.repeats,
        )
        return

    cleanup_stale_harbor_before_runs()
    resolved_experiments_root = resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    jobs = [
        (scenario_path, scenario_def, entry)
        for scenario_path, scenario_def in scenario_defs
        for entry in entries
    ]
    successes, failures = run_matrix_jobs(
        jobs,
        experiment_config=experiment_config,
        experiments_root=resolved_experiments_root,
        experiment_kind=options["experiment_kind"],
        parallel=options["parallel"],
    )
    click.echo(f"Matrix completed: {successes} experiments succeeded, {failures} failed.")


def validate_matrix_options(options: dict[str, object], scenario_paths: tuple[Path, ...]) -> None:
    if not scenario_paths:
        raise click.ClickException("At least one --scenario path is required.")
    if (options["config"] is None) == (options["selector"] is None):
        raise click.ClickException("Provide exactly one of --config or --selector.")


def matrix_config_from_options(options: dict[str, object]):
    from raidar.matrix import build_selected_matrix_config, load_matrix_config

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


def echo_matrix_settings(matrix_config, scenario_defs, entries) -> None:
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


def load_matrix_scenarios(
    scenario_paths: tuple[Path, ...],
) -> list[tuple[Path, ScenarioDefinition]]:
    scenario_defs: list[tuple[Path, ScenarioDefinition]] = []
    for scenario_path in scenario_paths:
        click.echo(f"Loading scenario from {scenario_path}")
        scenario_defs.append((scenario_path, load_scenario(scenario_path)))
    return scenario_defs


def echo_matrix_dry_run(
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


def matrix_job_options(request: dict[str, object]) -> RunCliOptions:
    entry = request["entry"]
    experiment_config = request["experiment_config"]
    return build_run_cli_options_from_request(
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


def run_matrix_jobs(
    jobs: list[tuple[Path, ScenarioDefinition, object]],
    **settings,
) -> tuple[int, int]:
    def run_matrix_job(job: tuple[Path, ScenarioDefinition, object]) -> SuiteExecutionResult:
        scenario_path, _scenario_def, entry = job
        options = matrix_job_options(
            {
                "scenario_path": scenario_path,
                "entry": entry,
                "experiment_config": settings["experiment_config"],
                "experiments_root": settings["experiments_root"],
                "experiment_kind": settings["experiment_kind"],
            }
        )
        return execute_run_options(
            options,
            force_experiment_summary=True,
            cleanup_before_runs=False,
            echo=False,
            execution_suffix=experiment_execution_suffix(options),
        )

    if settings["parallel"] > 1:
        return run_parallel_matrix_jobs(jobs, settings["parallel"], run_matrix_job)
    return run_sequential_matrix_jobs(jobs, run_matrix_job)


def run_parallel_matrix_jobs(jobs, parallel, run_matrix_job) -> tuple[int, int]:
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


def run_sequential_matrix_jobs(jobs, run_matrix_job) -> tuple[int, int]:
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


@click.command()
@click.option("--results", "-r", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--format", "-f", type=click.Choice(["json", "csv", "markdown"]), default="markdown")
@click.option("--output", "-o", type=click.Path(path_type=Path))
def report(results: Path, format: str, output: Path | None) -> None:
    """Generate a comparison report from experiment runs."""
    from raidar.storage import (
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
            output.write_text(report_text)
            click.echo(f"Report saved to {output}")
        else:
            click.echo(report_text)
    else:
        agg = aggregate_results(runs)
        if output:
            output.write_text(json.dumps(agg, indent=2))
            click.echo(f"JSON exported to {output}")
        else:
            click.echo(json.dumps(agg, indent=2))


@click.command()
def init_matrix() -> None:
    """Create example matrix configuration file."""
    from raidar.matrix import create_example_matrix

    output_path = Path("matrix.yaml")
    output_path.write_text(create_example_matrix())
    click.echo(f"Example matrix configuration created: {output_path}")
