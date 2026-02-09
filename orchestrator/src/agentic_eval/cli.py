"""CLI entrypoint for eval orchestrator."""

import concurrent.futures
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import click
from dotenv import load_dotenv

from .harness.config import Agent, HarnessConfig, ModelTarget
from .repeat_suite import (
    create_repeat_suite_summary,
    persist_repeat_suite,
    repeat_workspace,
)
from .runner import RunRequest, load_task, run_task
from .schemas.scorecard import EvalRun

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """Eval orchestrator for testing model/harness combinations."""
    pass


AGENT_CHOICES = [agent.value for agent in Agent]


def _summary_result_path(run: EvalRun) -> Path:
    run_meta = run.scores.metadata.get("run", {})
    summary_result = run_meta.get("summary_result_json")
    if not isinstance(summary_result, str):
        raise click.ClickException("Canonical summary result path missing from run metadata.")
    return Path(summary_result)


def _persist_eval_run(run: EvalRun) -> Path:
    result_path = _summary_result_path(run)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(run.model_dump_json(indent=2))
    return result_path


def _build_repeat_request(base_request: RunRequest, repeat_index: int) -> RunRequest:
    return RunRequest(
        task=base_request.task,
        config=base_request.config,
        scaffold_root=base_request.scaffold_root,
        task_dir=base_request.task_dir,
        workspace_dir=repeat_workspace(base_request.workspace_dir, repeat_index),
        results_dir=base_request.results_dir,
    )


def _execute_run_request(run_request: RunRequest) -> EvalRun:
    run = run_task(run_request)
    _persist_eval_run(run)
    return run


def _execute_repeat_batch(
    *,
    request: RunRequest,
    batch_size: int,
    repeat_parallel: int,
    start_index: int,
) -> list[EvalRun]:
    if batch_size <= 0:
        return []
    if repeat_parallel <= 1:
        return [
            _execute_run_request(_build_repeat_request(request, start_index + offset))
            for offset in range(batch_size)
        ]

    resolved_parallel = max(1, min(repeat_parallel, batch_size))
    by_index: dict[int, EvalRun] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=resolved_parallel) as executor:
        future_map = {
            executor.submit(
                _execute_run_request,
                _build_repeat_request(request, start_index + offset),
            ): offset
            for offset in range(batch_size)
        }
        for future in concurrent.futures.as_completed(future_map):
            offset = future_map[future]
            try:
                by_index[offset] = future.result()
            except Exception as exc:
                repeat_idx = start_index + offset
                raise click.ClickException(f"Repeat {repeat_idx} failed: {exc}") from exc
    return [by_index[idx] for idx in sorted(by_index)]


def _count_voided(runs: list[EvalRun]) -> int:
    return sum(1 for run in runs if run.scores.voided)


def _run_with_void_retries(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
) -> tuple[list[EvalRun], int, int]:
    all_runs: list[EvalRun] = []
    next_repeat_index = 1
    pending_batch = repeats
    retries_used = 0

    initial_runs = _execute_repeat_batch(
        request=request,
        batch_size=pending_batch,
        repeat_parallel=repeat_parallel,
        start_index=next_repeat_index,
    )
    all_runs.extend(initial_runs)
    pending_batch = _count_voided(initial_runs)
    next_repeat_index += len(initial_runs)

    while pending_batch > 0 and retries_used < retry_void:
        retries_used += 1
        retry_runs = _execute_repeat_batch(
            request=request,
            batch_size=pending_batch,
            repeat_parallel=repeat_parallel,
            start_index=next_repeat_index,
        )
        all_runs.extend(retry_runs)
        pending_batch = _count_voided(retry_runs)
        next_repeat_index += len(retry_runs)

    return all_runs, retries_used, pending_batch


def _run_repeat_requests(
    *,
    request: RunRequest,
    repeats: int,
    repeat_parallel: int,
) -> list[EvalRun]:
    return _execute_repeat_batch(
        request=request,
        batch_size=max(1, repeats),
        repeat_parallel=repeat_parallel,
        start_index=1,
    )


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
    type=click.Choice(AGENT_CHOICES),
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
    "--scaffolds-root",
    "-S",
    type=click.Path(exists=True, path_type=Path),
    default=Path("scaffolds"),
    help="Root directory containing versioned scaffolds",
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
@click.option(
    "--repeats",
    type=click.IntRange(min=1),
    default=1,
    help="Number of repeated runs for the same configuration",
)
@click.option(
    "--repeat-parallel",
    type=click.IntRange(min=1),
    default=1,
    help="Parallel workers for repeat runs",
)
@click.option(
    "--retry-void",
    type=click.IntRange(min=0),
    default=0,
    help="Retry budget for voided runs (rate limits/timeouts/harness failures)",
)
def run(
    task: Path,
    agent: str,
    model: str,
    rules: Literal["strict", "minimal", "none"],
    scaffolds_root: Path,
    workspace: Path,
    output: Path,
    timeout: int,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
) -> None:
    """Run a task with specified harness and model."""
    task = task.resolve()
    scaffolds_root = scaffolds_root.resolve()
    workspace = workspace.resolve()
    output = output.resolve()

    click.echo(f"Loading task from {task}")
    task_def = load_task(task)

    click.echo(f"Task: {task_def.name}")
    click.echo(f"Agent: {agent}")
    click.echo(f"Model: {model}")
    click.echo(f"Rules variant: {rules}")
    click.echo(f"Repeats: {repeats}")
    click.echo(f"Repeat parallelism: {repeat_parallel}")
    click.echo(f"Retry void budget: {retry_void}")

    config = HarnessConfig(
        agent=Agent(agent),
        model=ModelTarget.from_string(model),
        rules_variant=rules,
        timeout_sec=timeout,
    )

    output.mkdir(parents=True, exist_ok=True)

    request = RunRequest(
        task=task_def,
        config=config,
        scaffold_root=scaffolds_root,
        task_dir=task.parent,
        workspace_dir=workspace,
        results_dir=output,
    )
    click.echo("Running task...")
    started_at = datetime.now(UTC)
    try:
        runs, retries_used, unresolved_void = _run_with_void_retries(
            request=request,
            repeats=max(1, repeats),
            repeat_parallel=repeat_parallel,
            retry_void=retry_void,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if repeats == 1:
        result = runs[0]
        run_meta = result.scores.metadata.get("run", {})
        canonical_dir = run_meta.get("canonical_run_dir")
        if isinstance(canonical_dir, str):
            click.echo(f"Canonical run dir: {canonical_dir}")
        result_path = _summary_result_path(result)
        click.echo(f"Result saved to {result_path}")
        click.echo(f"Run ID: {result.id}")
        click.echo(f"Duration: {result.duration_sec:.1f}s")
        click.echo(f"Terminated early: {result.terminated_early}")
        click.echo(f"Void result: {result.scores.voided}")
        if result.scores.voided:
            click.echo(f"Void reasons: {result.scores.void_reasons}")
        if result.termination_reason:
            click.echo(f"Reason: {result.termination_reason}")
        return

    suite_summary = create_repeat_suite_summary(
        task_name=task_def.name,
        harness=agent,
        model=model,
        rules_variant=rules,
        repeats=repeats,
        repeat_parallel=max(1, min(repeat_parallel, repeats)),
        runs=runs,
        started_at=started_at,
        retry_void_limit=retry_void,
        retries_used=retries_used,
        unresolved_void_count=unresolved_void,
    )
    summary_path, readme_path = persist_repeat_suite(output, suite_summary)

    click.echo(f"Repeat suite summary: {summary_path}")
    click.echo(f"Repeat suite readme: {readme_path}")
    aggregate = suite_summary.get("aggregate", {})
    repeat_required = int(aggregate.get("repeat_required_count", 0) or 0)
    click.echo(f"Void retries used: {retries_used}")
    if repeat_required > 0:
        click.echo(f"Repeat required for {repeat_required} voided runs.")
    for run in runs:
        click.echo(
            f"Run {run.id}: voided={run.scores.voided}, "
            f"qualified={run.scores.qualification.passed}, "
            f"composite={run.scores.composite_score:.3f}, duration={run.duration_sec:.1f}s"
        )


@main.command()
@click.option(
    "--scaffold",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to a specific scaffold template/version directory",
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
    type=click.Choice(sorted(set(AGENT_CHOICES + ["copilot", "cursor", "pi"]))),
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
    help="Path to a specific scaffold template/version directory",
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
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    multiple=True,
    help="Path to task.yaml file (repeatable)",
)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to matrix configuration YAML",
)
@click.option(
    "--scaffolds-root",
    "-S",
    type=click.Path(exists=True, path_type=Path),
    default=Path("scaffolds"),
    help="Root directory containing versioned scaffolds",
)
@click.option(
    "--parallel",
    type=int,
    default=1,
    help="Number of parallel executions",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show matrix entries without running",
)
def matrix(
    task: tuple[Path, ...], config: Path, scaffolds_root: Path, parallel: int, dry_run: bool
) -> None:
    """Run evaluation matrix from configuration."""
    from .comparison.matrix_runner import MatrixRunner
    from .matrix import MatrixEntry, generate_matrix_entries, load_matrix_config

    task_paths = tuple(path.resolve() for path in task)
    if not task_paths:
        raise click.ClickException("At least one --task path is required.")
    task_defs = _load_matrix_tasks(task_paths)

    click.echo(f"Loading matrix from {config}")
    matrix_config = load_matrix_config(config)
    entries: list[MatrixEntry] = generate_matrix_entries(matrix_config)
    total_entries = len(entries)
    click.echo(
        f"Matrix defined for {len(matrix_config.runs)} harness/model pairs × "
        f"{len(matrix_config.rules_variants)} rule variants ({total_entries} runs)"
    )

    runner = MatrixRunner(
        tasks_dir=task_paths[0].parent,
        scaffolds_root=scaffolds_root,
        results_dir=Path(matrix_config.results_path),
        workspaces_dir=Path(matrix_config.workspace_base),
    )

    if len(task_defs) == 1:
        _run_single_task_matrix(
            runner=runner,
            task_defs=task_defs,
            matrix_config=matrix_config,
            parallel=parallel,
            dry_run=dry_run,
        )
        return

    _run_multi_task_matrix(
        runner=runner,
        task_defs=task_defs,
        entries=entries,
        parallel=parallel,
        dry_run=dry_run,
    )


def _load_matrix_tasks(task_paths: tuple[Path, ...]) -> list[tuple[Path, object]]:
    task_defs: list[tuple[Path, object]] = []
    for task_path in task_paths:
        click.echo(f"Loading task from {task_path}")
        task_defs.append((task_path, load_task(task_path)))
    return task_defs


def _run_single_task_matrix(
    *,
    runner,
    task_defs: list[tuple[Path, object]],
    matrix_config,
    parallel: int,
    dry_run: bool,
) -> None:
    task_path, task_def = task_defs[0]
    report = runner.run_matrix(
        task=task_def,
        matrix_config=matrix_config,
        parallel=parallel,
        dry_run=dry_run,
    )
    click.echo(
        f"Matrix completed ({task_path.name}): "
        f"{report.successful_runs} successes, {report.failed_runs} failures."
    )


def _run_multi_task_matrix(
    *,
    runner,
    task_defs: list[tuple[Path, object]],
    entries,
    parallel: int,
    dry_run: bool,
) -> None:
    configs = [entry.to_harness_config() for entry in entries]
    jobs = [(task_path, task_def, cfg) for task_path, task_def in task_defs for cfg in configs]
    click.echo(
        f"Running multi-task matrix: {len(task_defs)} tasks × {len(configs)} configs = "
        f"{len(jobs)} runs with parallel={parallel}"
    )
    successes = 0
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as executor:
        future_to_task = {
            executor.submit(
                runner.run_single,
                task_def,
                cfg,
                task_path.parent,
                dry_run,
            ): task_path
            for task_path, task_def, cfg in jobs
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task_path = future_to_task[future]
            try:
                result = future.result()
            except Exception as exc:
                click.echo(f"[{task_path.stem}] failed: {exc}")
                failures += 1
                continue
            if result.scorecard is not None:
                successes += 1
            elif result.error is not None:
                failures += 1
    click.echo(f"Multi-task matrix completed: {successes} successes, {failures} failures.")


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


@main.command()
def list_agents() -> None:
    """List supported agents and their rule files."""
    from .harness.rules import SYSTEM_RULES

    click.echo("Supported Agents:")
    for agent, rule_file in SYSTEM_RULES.items():
        click.echo(f"  {agent:15} -> {rule_file}")


def _echo_task_summary(task_def) -> None:
    click.echo(f"Task: {task_def.name}")
    click.echo(f"Description: {task_def.description}")
    click.echo(f"Difficulty: {task_def.difficulty}")
    click.echo(f"Category: {task_def.category}")
    click.echo(f"Timeout: {task_def.timeout_sec // 60} minutes")

    if task_def.verification.gates:
        gates = [g.name for g in task_def.verification.gates]
        click.echo(f"Quality Gates: {', '.join(gates)}")


def _echo_rule_variants(task_dir: Path) -> None:
    rules_dir = task_dir / "rules"
    if not rules_dir.exists():
        return
    click.echo()
    click.echo("Rule Variants:")
    for variant in ["strict", "minimal", "none"]:
        variant_dir = rules_dir / variant
        if variant_dir.exists():
            files = [f.name for f in variant_dir.iterdir() if f.is_file()]
            click.echo(f"  {variant}: {', '.join(files) or '(empty)'}")


def _echo_visual_config(task_def) -> None:
    if not task_def.visual:
        return
    click.echo()
    click.echo("Visual Config:")
    click.echo(f"  Reference: {task_def.visual.reference_image}")
    click.echo(f"  Threshold: {task_def.visual.threshold}")


def _echo_compliance_config(task_def) -> None:
    if not (task_def.compliance.deterministic_checks or task_def.compliance.llm_judge_rubric):
        return
    click.echo()
    click.echo("Compliance Config:")
    if task_def.compliance.deterministic_checks:
        click.echo(f"  Deterministic checks: {len(task_def.compliance.deterministic_checks)}")
    if task_def.compliance.llm_judge_rubric:
        click.echo(f"  LLM judge criteria: {len(task_def.compliance.llm_judge_rubric)}")


@main.command()
@click.option(
    "--task",
    "-t",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to task directory",
)
def info(task: Path) -> None:
    """Show task information and details."""
    from .schemas.task import TaskDefinition

    task_yaml = task / "task.yaml"
    if not task_yaml.exists():
        click.echo(f"Error: task.yaml not found in {task}", err=True)
        raise SystemExit(1)

    task_def = TaskDefinition.from_yaml(task_yaml)

    _echo_task_summary(task_def)
    _echo_rule_variants(task)
    _echo_visual_config(task_def)
    _echo_compliance_config(task_def)


if __name__ == "__main__":
    main()
