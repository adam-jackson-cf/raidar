"""Execute a plan (plan D2-D4): materialise, run, classify, time, record.

Run directory (``<runs_root>/<experiment id>/<run id>/``)::

    run.json                 run manifest: lock, options, selected trials, outcome summary
    plan.json                the full plan with every trial's hashes (D1)
    assessment-config.yaml   locked assessor settings for ``raidar assess --rescore``
    tasks/<trial>/           the materialised Harbor task of each trial
    trials/<trial>/          Harbor's trial directory (steps/, raidar-oracle/, result.json)
    trajectories/<trial>.json  TrajectoryRecord: CheckpointAttempts, validity, N1 phases
"""

from __future__ import annotations

import json
import os
import tomllib
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from raidar.execution.agent_config import reported_harness_pin
from raidar.execution.assessment import write_assessment_config
from raidar.execution.driver import TrialRunner, run_harbor_trial, run_in_order
from raidar.execution.materialise import MaterialisedTask, materialise_task
from raidar.execution.models import PlannedTrial, TrajectoryRecord
from raidar.execution.plan import ExperimentPlan
from raidar.execution.trajectory import (
    RunnerFailure,
    TrialEvidence,
    build_record,
    runner_failure_record,
    write_record,
)
from raidar.execution.trial_config import harbor_trial_config
from raidar.packs.python import image_present
from raidar.problems.store import ImportedProblem, ProblemStore

DEFAULT_DOCKER_CONTEXT = "orbstack"
RUNNER_ERROR_FILE = "raidar-runner-error.txt"


@dataclass
class RunContext:
    plan: ExperimentPlan
    run_dir: Path
    run_id: str
    problems: Mapping[int, ImportedProblem]
    runner: TrialRunner = run_harbor_trial
    docker_context: str = field(
        default_factory=lambda: os.environ.get("DOCKER_CONTEXT", DEFAULT_DOCKER_CONTEXT)
    )

    @property
    def trials_dir(self) -> Path:
        return self.run_dir / "trials"


def load_problems(plan: ExperimentPlan, store: ProblemStore) -> dict[int, ImportedProblem]:
    """Every planned problem, content-verified and oracle-validated (F13)."""
    loaded = {}
    for target in plan.problems:
        problem = store.load(target.ref)
        store.require_validated(target.ref, problem.manifest)
        loaded[target.index] = problem
    return loaded


def _environment_image(task: Path) -> str | None:
    document = tomllib.loads((task / "task.toml").read_text())
    image = (document.get("environment") or {}).get("docker_image")
    return image if isinstance(image, str) else None


def _materialise(ctx: RunContext, trial: PlannedTrial) -> MaterialisedTask:
    problem = ctx.problems[trial.problem_index]
    override = ctx.plan.options.agent_override
    return materialise_task(
        problem.task_dir,
        ctx.run_dir / "tasks" / trial.trial_id,
        steps=trial.steps,
        rule=ctx.plan.rule,
        condition=(ctx.plan.loaded, trial.arm, trial.hashes.condition),
        references={s: problem.reference(s) for s in trial.steps} if override == "oracle" else None,
    )


def _planned_steps(ctx: RunContext, trial: PlannedTrial) -> tuple[tuple[str, str], ...]:
    """(step, checkpoint) for every checkpoint of the trial's problem, in order."""
    problem = ctx.problems[trial.problem_index]
    checkpoints = {step.name: step.checkpoint for step in problem.bundle.manifest.steps}
    return tuple((step, checkpoints[step]) for step in ctx.plan.problems[trial.problem_index].steps)


async def _run_and_classify(ctx: RunContext, trial: PlannedTrial) -> TrajectoryRecord:
    plan = ctx.plan
    problem = ctx.problems[trial.problem_index]
    task = _materialise(ctx, trial)
    image = _environment_image(task.path)
    cold = image is not None and not image_present(image)
    config = harbor_trial_config(
        plan,
        trial,
        task.path,
        trials_dir=ctx.trials_dir,
        bundle_root=problem.bundle.root,
        docker_context=ctx.docker_context,
    )
    outcome = await ctx.runner(config, problem.bundle.root)
    evidence = TrialEvidence(
        trial=trial,
        trial_dir=outcome.trial_dir,
        task=task,
        planned_steps=_planned_steps(ctx, trial),
        pinned_harness_version=reported_harness_pin(plan.arms[trial.arm].agent_config),
        environment_cold=cold,
        runner_exception=outcome.exception_type,
    )
    return build_record(
        evidence,
        experiment=plan.experiment_id,
        run_id=ctx.run_id,
        run_dir=ctx.run_dir,
        rule=plan.rule,
        max_checkpoints=plan.options.max_checkpoints,
        agent_override=plan.options.agent_override,
    )


def _failure_record(ctx: RunContext, trial: PlannedTrial, error: Exception) -> TrajectoryRecord:
    """Record a trial whose Raidar side raised, keeping its traceback beside the trial."""
    trial_dir = ctx.trials_dir / trial.trial_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / RUNNER_ERROR_FILE).write_text(
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    plan = ctx.plan
    return runner_failure_record(
        RunnerFailure(trial, _planned_steps(ctx, trial), trial_dir, error),
        experiment=plan.experiment_id,
        run_id=ctx.run_id,
        run_dir=ctx.run_dir,
        rule=plan.rule,
        max_checkpoints=plan.options.max_checkpoints,
        agent_override=plan.options.agent_override,
    )


async def run_trial(ctx: RunContext, trial: PlannedTrial) -> TrajectoryRecord:
    """Materialise, run through Harbor, classify, time and record one trial.

    A failure on the Raidar side (materialising, ``Trial.create``, the leak
    guard, reading the trial back) is contained: the trial is recorded as an
    invalid, infra-error trajectory and its siblings keep running.
    """
    try:
        record = await _run_and_classify(ctx, trial)
    except Exception as error:  # noqa: BLE001 - contained and recorded as infra, never lost
        record = _failure_record(ctx, trial, error)
    return write_record(ctx.run_dir / "trajectories" / f"{trial.trial_id}.json", record)


def _summary(records: list[TrajectoryRecord]) -> dict[str, object]:
    states: dict[str, int] = {}
    for record in records:
        for attempt in record.attempts:
            states[attempt.checkpoint_state.value] = (
                states.get(attempt.checkpoint_state.value, 0) + 1
            )
    return {
        "trajectories": len(records),
        "valid": sum(1 for record in records if record.valid),
        "checkpoint_states": dict(sorted(states.items())),
    }


def default_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


async def execute(ctx: RunContext) -> list[TrajectoryRecord]:
    """Run the plan's selected trials and write the run directory."""
    ctx.run_dir.mkdir(parents=True, exist_ok=False)
    plan = ctx.plan
    _write_json(ctx.run_dir / "plan.json", plan.to_json())
    write_assessment_config(plan.loaded.spec.assessment, ctx.run_dir)
    manifest: dict[str, object] = {
        "kind": "raidar-run",
        "experiment": plan.experiment_id,
        "run_id": ctx.run_id,
        "lock_sha256": plan.lock_sha256,
        "experiment_file": str(plan.loaded.path),
        "docker_context": ctx.docker_context,
        "concurrency": plan.concurrency,
        "selected": [trial.trial_id for trial in plan.selected_trials()],
        "partial": len(plan.selected) != len(plan.trials),
        "started_at": datetime.now(UTC).isoformat(),
    }
    _write_json(ctx.run_dir / "run.json", manifest)
    finished: list[TrajectoryRecord] = []

    async def run_one(trial: PlannedTrial) -> TrajectoryRecord:
        record = await run_trial(ctx, trial)
        finished.append(record)
        return record

    try:
        return await run_in_order(plan.selected_trials(), run_one, concurrency=plan.concurrency)
    except BaseException as error:
        manifest["aborted"] = type(error).__name__
        raise
    finally:
        manifest |= {"finished_at": datetime.now(UTC).isoformat(), "summary": _summary(finished)}
        _write_json(ctx.run_dir / "run.json", manifest)
