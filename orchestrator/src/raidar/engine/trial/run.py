"""One trial of a job: materialise, run through Harbor, classify and record it.

``raidar.engine.trial.context.JobContext`` is what a job's trials share;
``raidar.engine.job.run`` schedules the trials and ``raidar.engine.trial.finish`` audits
and writes each record.
"""

from __future__ import annotations

import asyncio
import traceback

from raidar.contract.job_layout import TASKS_DIR
from raidar.contract.trial import PlannedTrial, TrialRecord
from raidar.engine.trial.agent_config import reported_harness_pin
from raidar.engine.trial.config import harbor_trial_config
from raidar.engine.trial.context import JobContext
from raidar.engine.trial.finish import finish_trial
from raidar.engine.trial.materialise import (
    MaterialisedTask,
    environment_cold,
    environment_image_id,
    materialise_task,
)
from raidar.engine.trial.provision import TrialProvision
from raidar.engine.trial.record import (
    RunnerFailure,
    TrialEvidence,
    build_record,
    runner_failure_record,
)
from raidar.engine.trial.workspace import workspace_seed

RUNNER_ERROR_FILE = "raidar-runner-error.txt"


def _materialise(ctx: JobContext, trial: PlannedTrial) -> MaterialisedTask:
    problem = ctx.problems[trial.problem_index]
    override = ctx.plan.options.agent_override
    return materialise_task(
        problem.task_dir,
        ctx.job_dir / TASKS_DIR / trial.trial_id,
        steps=trial.steps,
        rule=ctx.plan.rule,
        arm=(ctx.plan.loaded, trial.arm, trial.hashes.arm),
        references={s: problem.reference(s) for s in trial.steps} if override == "oracle" else None,
    )


def _planned_steps(ctx: JobContext, trial: PlannedTrial) -> tuple[tuple[str, str], ...]:
    """(step, checkpoint) for every checkpoint of the trial's problem, in order."""
    problem = ctx.problems[trial.problem_index]
    checkpoints = {step.name: step.checkpoint for step in problem.bundle.manifest.steps}
    return tuple((step, checkpoints[step]) for step in ctx.plan.problems[trial.problem_index].steps)


def _provisions(ctx: JobContext, trial: PlannedTrial) -> tuple[TrialProvision, ...]:
    """The lab's per-trial work before checkpoint 1: seeding the workspace project (D2)."""
    manifest = ctx.problems[trial.problem_index].bundle.manifest
    return (
        workspace_seed(manifest.problem, manifest.grader.test_dependencies, ctx.plan.pack.python),
    )


async def _run_and_classify(ctx: JobContext, trial: PlannedTrial) -> TrialRecord:
    plan = ctx.plan
    problem = ctx.problems[trial.problem_index]
    task = _materialise(ctx, trial)
    cold = await asyncio.to_thread(environment_cold, task.path)
    config = harbor_trial_config(
        plan,
        trial,
        task.path,
        trials_dir=ctx.trials_dir,
        bundle_root=problem.bundle.root,
        docker_context=ctx.docker_context,
        errata=plan.problems[trial.problem_index].errata,
    )
    provisions = _provisions(ctx, trial)
    outcome = await ctx.runner(config, problem.bundle.root, provisions)
    timeouts = {
        "agent_timeout_s": config.agent.override_timeout_sec,
        "verifier_timeout_s": config.verifier.override_timeout_sec,
        "grader_timeout_s": config.verifier.kwargs.get("grader_timeout_s"),
    }
    evidence = TrialEvidence(
        trial=trial,
        trial_dir=outcome.trial_dir,
        task=task,
        provisions=tuple(provision.slug for provision in provisions),
        planned_steps=_planned_steps(ctx, trial),
        pinned_harness_version=reported_harness_pin(plan.arms[trial.arm].agent_config),
        environment_cold=cold,
        runner_exception=outcome.exception_type,
        image_id=await asyncio.to_thread(environment_image_id, task.path),
        timeouts=timeouts,
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
    return await finish_trial(ctx, trial, record)
