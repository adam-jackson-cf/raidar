"""``raidar plan`` and ``raidar run EXPERIMENT`` (plan WS-D); ``raidar perf`` lives in ``perf``."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from raidar.cli.plan_build import build_cli_plan
from raidar.cli.target_options import job_target, run_guarded, target_option
from raidar.contract.paths import jobs_root
from raidar.engine.job.detach import default_job_id
from raidar.engine.job.plan import ExperimentPlan
from raidar.engine.job.run import run_job
from raidar.engine.job.run_checks import RunRefusedError, prepare_run
from raidar.engine.trial.agent_config import AGENT_OVERRIDES


def register(main: click.Group) -> None:
    main.add_command(plan_command)
    main.add_command(run_command)


def _plan_options(command: Callable[..., Any]) -> Callable[..., Any]:
    decorators = [
        click.argument("experiment", type=click.Path(exists=True, dir_okay=False, path_type=Path)),
        click.option("--attempts", type=int, default=None, help="Override analysis.attempts."),
        click.option("--seed", type=int, default=None, help="Override analysis.seed."),
        click.option(
            "--concurrency",
            type=click.IntRange(min=1),
            default=None,
            help="Trials running at once on this machine (default: analysis.concurrency). "
            "Execution-only: never changes the lock or any trial hash.",
        ),
        click.option(
            "--set",
            "assignments",
            multiple=True,
            metavar="KEY=VALUE",
            help="Dotted-path spec override; wins over flags, which win over the file.",
        ),
        click.option(
            "--lock",
            "lock_path",
            type=click.Path(dir_okay=False, path_type=Path),
            default=None,
            help="Lock file (default: <experiment name>.lock.json next to the experiment).",
        ),
        click.option("--arm", "arms", multiple=True, help="Run only these arms (repeatable)."),
        click.option(
            "--attempt",
            "attempts_selected",
            type=int,
            multiple=True,
            help="Run only these attempts.",
        ),
        click.option(
            "--max-checkpoints",
            type=click.IntRange(min=1),
            default=None,
            help="Truncate every trial to its first N checkpoints (dev/validation).",
        ),
        click.option(
            "--agent-override",
            type=click.Choice(AGENT_OVERRIDES),
            default=None,
            help="Replace every arm's agent with Harbor's no-LLM agent (perf/CI fixture jobs).",
        ),
    ]
    for decorator in reversed(decorators):
        command = decorator(command)
    return command


def _echo_plan(plan: ExperimentPlan) -> None:
    click.echo(f"Plan for {plan.experiment_id} (lock {plan.lock_sha256[:12]})")
    click.echo(f"  progression: {plan.rule.mode}  concurrency: {plan.concurrency}")
    options = plan.options
    if options.max_checkpoints or options.agent_override:
        click.echo(
            f"  truncated_to: {options.max_checkpoints}  agent_override: {options.agent_override}"
        )
    for arm_id, blueprint in plan.arms.items():
        click.echo(
            f"  arm {arm_id}: condition {blueprint.compiled.condition_id} "
            f"digest {blueprint.compiled.digest[:12]} agent "
            f"{blueprint.agent_config.name or blueprint.agent_config.import_path}"
        )
    for trial in plan.trials:
        mark = "*" if trial.trial_id in plan.selected else " "
        hashes = trial.hashes
        click.echo(
            f" {mark} {trial.sequence:03d} {trial.trial_id} "
            f"attempt={trial.attempt} order={trial.order} "
            f"steps={len(trial.steps)} trial={hashes.trial[:12]} condition={hashes.condition[:12]} "
            f"arm={hashes.arm[:12]} problem={hashes.problem[:12]} agent={hashes.agent[:12]} "
            f"protocol={hashes.protocol[:12]}"
        )
    click.echo(f"  {len(plan.selected)}/{len(plan.trials)} trials selected (*)")


@click.command("plan")
@_plan_options
@click.option("--json", "as_json", is_flag=True, help="Emit the plan as JSON.")
def plan_command(as_json: bool, **options: Any) -> None:
    """Dry run: list every trial of a locked experiment with its input hashes."""
    plan, _ = build_cli_plan(options)
    if as_json:
        click.echo(json.dumps(plan.to_json(), indent=2, sort_keys=True))
        return
    _echo_plan(plan)


@click.command("run")
@_plan_options
@click.option(
    "--jobs-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=jobs_root(),
    show_default=True,
    help="Root for job directories (<jobs-dir>/<experiment id>/<job id>/).",
)
@click.option("--job-id", default=None, help="Job directory name (default: UTC timestamp).")
@click.option(
    "--allow-unsmoked",
    is_flag=True,
    help="Run a non-smoke experiment without a clean matching smoke job (recorded in job.json).",
)
@target_option
def run_command(
    jobs_dir: Path,
    job_id: str | None,
    target_name: str | None,
    allow_unsmoked: bool,
    **options: Any,
) -> None:
    """Run a locked experiment.yaml: every selected trial through Harbor."""
    plan, store = build_cli_plan(options)
    if not plan.selected:
        raise click.ClickException("the selection matches no trial")
    jt = job_target(target_name)
    job_id = job_id or default_job_id()
    try:
        problems, admission = prepare_run(plan, store, jt, jobs_dir, allow_unsmoked=allow_unsmoked)
    except RunRefusedError as error:
        raise click.ClickException(str(error)) from error
    job_dir = jobs_dir.resolve() / plan.experiment_id / job_id
    records = run_guarded(
        job_dir,
        run_job(plan, problems, job_dir=job_dir, job_id=job_id, target=jt, admission=admission),
    )
    click.echo(f"Job {job_dir}")
    for record in records:
        states = " ".join(a.checkpoint_state.value for a in record.step_results)
        click.echo(
            f"  {record.trial.trial_id}: valid={record.valid} reached={sum(record.reached)} "
            f"[{states}]"
        )
