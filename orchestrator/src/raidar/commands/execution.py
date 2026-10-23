"""``raidar plan`` and ``raidar run EXPERIMENT`` (plan WS-D); ``raidar perf`` lives in ``perf``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from raidar.commands.experiment_spec import _load
from raidar.commands.perf import perf_group
from raidar.execution.agent_config import AGENT_OVERRIDES, AgentOverride
from raidar.execution.credentials import CredentialPreflightError, preflight_credentials
from raidar.execution.plan import ExperimentPlan, PlanError, PlanOptions, build_plan
from raidar.execution.run import JobContext, default_job_id, execute, load_problems
from raidar.packs.drift import LockedPackError, lock_runtime_pack
from raidar.problems.store import ProblemStore, ProblemStoreError, find_store_root
from raidar.spec.lock import (
    LockError,
    StaleLockError,
    build_lock,
    check_lock_file,
    default_lock_path,
    render_lock,
)


def register(main: click.Group) -> None:
    main.add_command(plan_command)
    main.add_command(run_command)
    main.add_command(perf_group)


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


def _current_lock(loaded: Any, lock_path: Path) -> dict[str, Any]:
    """The lock, refused unless relocking the experiment reproduces it byte for byte.

    The refusal names each stale section; an assessment-only change relocks to
    the same ``lock_sha256``, so earlier runs still match the new lock. Only the
    host's Python patch release (``toolchain.python``, outside ``lock_sha256``)
    may differ from a committed lock, as ``make experiment-locks-check`` allows;
    the job records the host's value in its own ``job.lock.json``.
    """
    try:
        current = build_lock(loaded, lock_path=lock_path)
        note = check_lock_file(lock_path, current, ignore_host_python=True)
    except (LockError, StaleLockError) as exc:
        raise click.ClickException(str(exc)) from exc
    if note:
        click.echo(f"note: {note}", err=True)
    return dict(json.loads(render_lock(current)))


def _build(options: dict[str, Any]) -> tuple[ExperimentPlan, ProblemStore]:
    loaded = _load(
        options["experiment"],
        options["attempts"],
        options["seed"],
        options["assignments"],
    )
    lock = _current_lock(loaded, options["lock_path"] or default_lock_path(loaded.path))
    override: AgentOverride | None = options["agent_override"]
    try:
        store = ProblemStore(find_store_root(loaded.base_dir))
        plan = build_plan(
            loaded,
            lock,
            store=store,
            pack=lock_runtime_pack(lock),
            options=PlanOptions(
                arms=tuple(options["arms"]),
                attempts=tuple(options["attempts_selected"]),
                max_checkpoints=options["max_checkpoints"],
                agent_override=override,
                concurrency=options["concurrency"],
            ),
        )
    except (LockedPackError, PlanError, ProblemStoreError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    return plan, store


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
    plan, _ = _build(options)
    if as_json:
        click.echo(json.dumps(plan.to_json(), indent=2, sort_keys=True))
        return
    _echo_plan(plan)


@click.command("run")
@_plan_options
@click.option(
    "--jobs-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("jobs"),
    show_default=True,
    help="Root for job directories (<jobs-dir>/<experiment id>/<job id>/).",
)
@click.option("--job-id", default=None, help="Job directory name (default: UTC timestamp).")
def run_command(jobs_dir: Path, job_id: str | None, **options: Any) -> None:
    """Run a locked experiment.yaml: every selected trial through Harbor."""
    plan, store = _build(options)
    if not plan.selected:
        raise click.ClickException("the selection matches no trial")
    try:
        preflight_credentials(plan)
    except CredentialPreflightError as exc:
        raise click.ClickException(str(exc)) from exc
    job_id = job_id or default_job_id()
    try:
        problems = load_problems(plan, store)
    except ProblemStoreError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx = JobContext(
        plan=plan,
        job_dir=jobs_dir.resolve() / plan.experiment_id / job_id,
        job_id=job_id,
        problems=problems,
    )
    records = asyncio.run(execute(ctx))
    click.echo(f"Job {ctx.job_dir}")
    for record in records:
        states = " ".join(a.checkpoint_state.value for a in record.step_results)
        click.echo(
            f"  {record.trial.trial_id}: valid={record.valid} reached={sum(record.reached)} "
            f"[{states}]"
        )
