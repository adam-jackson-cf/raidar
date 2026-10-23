"""One trial of a job: materialise, run through Harbor, classify, time and record it.

``JobContext`` is what a job's trials share (plan, directories, problems, the
execution target and its guard); ``raidar.execution.run`` schedules the trials.
"""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from raidar.execution.agent_config import reported_harness_pin
from raidar.execution.driver import TrialRunner, run_harbor_trial
from raidar.execution.materialise import MaterialisedTask, environment_cold, materialise_task
from raidar.execution.models import PlannedTrial, TrialRecord
from raidar.execution.plan import ExperimentPlan
from raidar.execution.trial_config import harbor_trial_config
from raidar.execution.trial_record import (
    RunnerFailure,
    TrialEvidence,
    build_record,
    runner_failure_record,
    write_record,
)
from raidar.job_layout import TRIAL_RECORDS_DIR, TRIALS_DIR
from raidar.problems.store import ImportedProblem
from raidar.targets.job import JobTarget

RUNNER_ERROR_FILE = "raidar-runner-error.txt"


@dataclass
class JobContext:
    plan: ExperimentPlan
    job_dir: Path
    job_id: str
    problems: Mapping[int, ImportedProblem]
    target: JobTarget
    runner: TrialRunner = run_harbor_trial

    @property
    def docker_context(self) -> str:
        """The grader target's context: where the verifier's hidden-test grader runs."""
        return self.target.selection.grader.docker_context

    @property
    def trials_dir(self) -> Path:
        return self.job_dir / TRIALS_DIR


def _materialise(ctx: JobContext, trial: PlannedTrial) -> MaterialisedTask:
    problem = ctx.problems[trial.problem_index]
    override = ctx.plan.options.agent_override
    return materialise_task(
        problem.task_dir,
        ctx.job_dir / "tasks" / trial.trial_id,
        steps=trial.steps,
        rule=ctx.plan.rule,
        condition=(ctx.plan.loaded, trial.arm, trial.hashes.condition),
        references={s: problem.reference(s) for s in trial.steps} if override == "oracle" else None,
    )


def _planned_steps(ctx: JobContext, trial: PlannedTrial) -> tuple[tuple[str, str], ...]:
    """(step, checkpoint) for every checkpoint of the trial's problem, in order."""
    problem = ctx.problems[trial.problem_index]
    checkpoints = {step.name: step.checkpoint for step in problem.bundle.manifest.steps}
    return tuple((step, checkpoints[step]) for step in ctx.plan.problems[trial.problem_index].steps)


async def _run_and_classify(ctx: JobContext, trial: PlannedTrial) -> TrialRecord:
    plan = ctx.plan
    problem = ctx.problems[trial.problem_index]
    task = _materialise(ctx, trial)
    cold = environment_cold(task.path)
    config = harbor_trial_config(
        plan,
        trial,
        task.path,
        trials_dir=ctx.trials_dir,
        bundle_root=problem.bundle.root,
        docker_context=ctx.docker_context,
        errata=plan.problems[trial.problem_index].errata,
    )
    outcome = await ctx.runner(config, problem.bundle.root, task.provisions)
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
        job_id=ctx.job_id,
        job_dir=ctx.job_dir,
        rule=plan.rule,
        max_checkpoints=plan.options.max_checkpoints,
        agent_override=plan.options.agent_override,
    )


def _failure_record(ctx: JobContext, trial: PlannedTrial, error: Exception) -> TrialRecord:
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
        job_id=ctx.job_id,
        job_dir=ctx.job_dir,
        rule=plan.rule,
        max_checkpoints=plan.options.max_checkpoints,
        agent_override=plan.options.agent_override,
    )


async def run_trial(ctx: JobContext, trial: PlannedTrial) -> TrialRecord:
    """Materialise, run through Harbor, classify, time and record one trial.

    A failure on the Raidar side (materialising, ``Trial.create``, the leak
    guard, reading the trial back) is contained: the trial is recorded as an
    invalid, infra-error trial and its siblings keep running.
    """
    try:
        record = await _run_and_classify(ctx, trial)
    except Exception as error:  # noqa: BLE001 - contained and recorded as infra, never lost
        record = _failure_record(ctx, trial, error)
    if ctx.target.guard is not None:
        record = await ctx.target.guard.review(
            record,
            trial_dir=ctx.trials_dir / trial.trial_id,
            trials_dir=ctx.trials_dir,
        )
    return write_record(ctx.job_dir / TRIAL_RECORDS_DIR / f"{trial.trial_id}.json", record)
