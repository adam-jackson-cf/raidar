"""Execute a plan (plan D2-D4): materialise, run, classify, time, record.

Writes one job directory (``<jobs_root>/<experiment id>/<job id>/``); the
layout is ``raidar.job_layout``.
"""

from __future__ import annotations

import json
import os
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from raidar.execution.agent_config import reported_harness_pin
from raidar.execution.assessment import lock_errata, write_assessment_config
from raidar.execution.driver import TrialRunner, run_harbor_trial, run_in_order
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
from raidar.job_layout import (
    DETACH_DIR,
    JOB_KIND,
    JOB_LOCK_FILE,
    JOB_MANIFEST,
    PLAN_FILE,
    TRIAL_RECORDS_DIR,
    TRIALS_DIR,
)
from raidar.problems.store import ImportedProblem, ProblemStore

DEFAULT_DOCKER_CONTEXT = "orbstack"
RUNNER_ERROR_FILE = "raidar-runner-error.txt"


@dataclass
class JobContext:
    plan: ExperimentPlan
    job_dir: Path
    job_id: str
    problems: Mapping[int, ImportedProblem]
    runner: TrialRunner = run_harbor_trial
    docker_context: str = field(
        default_factory=lambda: os.environ.get("DOCKER_CONTEXT", DEFAULT_DOCKER_CONTEXT)
    )

    @property
    def trials_dir(self) -> Path:
        return self.job_dir / TRIALS_DIR


def load_problems(plan: ExperimentPlan, store: ProblemStore) -> dict[int, ImportedProblem]:
    """Every planned problem, content-verified and verifier-validated (F13)."""
    loaded = {}
    for target in plan.problems:
        problem = store.load(target.ref)
        store.require_validated(target.ref, problem.manifest)
        loaded[target.index] = problem
    return loaded


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
    return write_record(ctx.job_dir / TRIAL_RECORDS_DIR / f"{trial.trial_id}.json", record)


def _summary(records: list[TrialRecord]) -> dict[str, object]:
    states: dict[str, int] = {}
    for record in records:
        for step_result in record.step_results:
            states[step_result.checkpoint_state.value] = (
                states.get(step_result.checkpoint_state.value, 0) + 1
            )
    return {
        "trials": len(records),
        "valid": sum(1 for record in records if record.valid),
        "checkpoint_states": dict(sorted(states.items())),
    }


def default_job_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def create_job_dir(job_dir: Path) -> None:
    """Create the job directory; a job directory is never reused.

    The one exception is a detached launch (``raidar.execution.detach``), which
    writes its ``detach/`` files (launch record, pid, log) before the job starts:
    a directory holding exactly that is this job's own.
    """
    if job_dir.is_dir() and [path.name for path in job_dir.iterdir()] == [DETACH_DIR]:
        return
    job_dir.mkdir(parents=True, exist_ok=False)


async def execute(ctx: JobContext) -> list[TrialRecord]:
    """Run the plan's selected trials and write the job directory."""
    create_job_dir(ctx.job_dir)
    plan = ctx.plan
    _write_json(ctx.job_dir / PLAN_FILE, plan.to_json())
    if plan.lock:
        _write_json(ctx.job_dir / JOB_LOCK_FILE, plan.lock)
    write_assessment_config(
        plan.loaded.spec.assessment,
        plan.loaded.spec.protocol,
        ctx.job_dir,
        lock_errata(plan.lock),
    )
    manifest: dict[str, object] = {
        "kind": JOB_KIND,
        "experiment": plan.experiment_id,
        "job_id": ctx.job_id,
        "lock_sha256": plan.lock_sha256,
        "experiment_file": str(plan.loaded.path),
        "docker_context": ctx.docker_context,
        "concurrency": plan.concurrency,
        "selected": [trial.trial_id for trial in plan.selected_trials()],
        "partial": len(plan.selected) != len(plan.trials),
        "started_at": datetime.now(UTC).isoformat(),
    }
    _write_json(ctx.job_dir / JOB_MANIFEST, manifest)
    finished: list[TrialRecord] = []

    async def run_one(trial: PlannedTrial) -> TrialRecord:
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
        _write_json(ctx.job_dir / JOB_MANIFEST, manifest)
