"""Matrix execution and report CLI commands."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import click
from pydantic import ValidationError

from raidar.application.execution import build_run_cli_options_from_request
from raidar.application.models import RunCliOptions, RunCliOptionsBuildRequest, SuiteExecutionResult
from raidar.commands.shared import (
    EXPERIMENT_KIND_CHOICES,
    REPO_ROOT,
    cleanup_stale_harbor_before_runs,
    execute_run_options,
    experiment_execution_suffix,
    resolve_experiments_root,
)
from raidar.matrix import MatrixJob


def register(main: click.Group) -> None:
    main.add_command(matrix)
    main.add_command(report)
    main.add_command(init_matrix)


@click.command()
@click.option("--config", "-c", type=click.Path(exists=True, path_type=Path), required=True)
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
    from raidar.matrix import resolve_matrix_jobs

    matrix_config = matrix_config_from_options(options)
    try:
        jobs = resolve_matrix_jobs(matrix_config, repo_root=REPO_ROOT)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    experiment_config = matrix_config.experiment
    echo_matrix_settings(matrix_config, jobs)

    if options["dry_run"]:
        echo_matrix_dry_run(
            jobs=jobs,
            repeats=experiment_config.repeats,
        )
        return

    cleanup_stale_harbor_before_runs()
    resolved_experiments_root = resolve_experiments_root(
        experiments_root=options["experiments_root"],
        experiment_kind=options["experiment_kind"],
    )
    successes, failures = run_matrix_jobs(
        jobs,
        experiment_config=experiment_config,
        experiments_root=resolved_experiments_root,
        experiment_kind=options["experiment_kind"],
        parallel=options["parallel"],
    )
    click.echo(f"Matrix completed: {successes} experiments succeeded, {failures} failed.")


def matrix_config_from_options(options: dict[str, object]):
    from raidar.matrix import load_matrix_config

    click.echo(f"Loading matrix from {options['config']}")
    try:
        return load_matrix_config(options["config"])
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc


def echo_matrix_settings(matrix_config, jobs: list[MatrixJob]) -> None:
    click.echo(f"Matrix '{matrix_config.id}' defined for {len(jobs)} experiments")
    experiment_config = matrix_config.experiment
    click.echo(
        "Experiment settings: "
        f"timeout={experiment_config.timeout_sec}s, repeats={experiment_config.repeats}, "
        "repeat_parallel="
        f"{experiment_config.repeat_parallel}, rerun_unscored={experiment_config.retry_void}"
    )


def echo_matrix_dry_run(
    *,
    jobs: list[MatrixJob],
    repeats: int,
) -> None:
    for job in jobs:
        entry = job.agent
        reasoning_label = f" [{entry.reasoning_effort}]" if entry.reasoning_effort else ""
        click.echo(
            f"[dry-run] {job.entry_id}: {job.scenario.name}@{job.scenario.scenario_revision}: "
            f"{entry.harness}/{entry.provider}/{entry.model}{reasoning_label} x{repeats}"
        )


def matrix_job_options(request: dict[str, object]) -> RunCliOptions:
    job = request["job"]
    entry = job.agent
    experiment_config = request["experiment_config"]
    return build_run_cli_options_from_request(
        RunCliOptionsBuildRequest(
            scenario=job.scenario_path,
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
    jobs: list[MatrixJob],
    **settings,
) -> tuple[int, int]:
    def run_matrix_job(job: MatrixJob) -> SuiteExecutionResult:
        options = matrix_job_options(
            {
                "job": job,
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
            job = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                click.echo(f"[{job.entry_id}] {job.agent.harness}/{job.agent.model} failed: {exc}")
                failures += 1
                continue
            successes += 1
            click.echo(
                f"[{job.entry_id}] {job.agent.harness}/{job.agent.model} -> {result.summary_path}"
            )
    return successes, failures


def run_sequential_matrix_jobs(jobs, run_matrix_job) -> tuple[int, int]:
    successes = 0
    failures = 0
    for job in jobs:
        click.echo(
            f"Running experiment: {job.entry_id} "
            f"{job.scenario.name}@{job.scenario.scenario_revision} "
            f"{job.agent.harness}/{job.agent.model}"
        )
        try:
            result = run_matrix_job(job)
        except Exception as exc:
            click.echo(f"[{job.entry_id}] {job.agent.harness}/{job.agent.model} failed: {exc}")
            failures += 1
            continue
        successes += 1
        click.echo(f"[{job.entry_id}] experiment summary: {result.summary_path}")
    return successes, failures


@click.command()
@click.option("--results", "-r", type=click.Path(exists=True, path_type=Path), required=True)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["json", "csv", "markdown"]),
    default="markdown",
)
@click.option("--output", "-o", type=click.Path(path_type=Path))
def report(results: Path, output_format: str, output: Path | None) -> None:
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

    if output_format == "csv":
        out_path = output or (results / "comparison.csv")
        export_to_csv(runs, out_path)
        click.echo(f"CSV exported to {out_path}")
    elif output_format == "markdown":
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
