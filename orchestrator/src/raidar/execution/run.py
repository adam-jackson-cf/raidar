"""Execute a plan (plan D2-D4): schedule the trials of a job and write the job directory.

Writes one job directory (``<jobs_root>/<experiment id>/<job id>/``); the
layout is ``raidar.core.job_layout``. One trial is ``raidar.execution.trial_run``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.core.job_layout import (
    DETACH_DIR,
    JOB_MANIFEST,
    TRIAL_RECORDS_DIR,
)
from raidar.execution.driver import run_in_order
from raidar.execution.health.records_manifest import seal_trial
from raidar.execution.job_context import JobContext
from raidar.execution.job_manifest import start_job, write_json
from raidar.execution.models import PlannedTrial, TrialRecord
from raidar.execution.plan import ExperimentPlan
from raidar.execution.trial_run import run_trial
from raidar.problems.store import ImportedProblem, ProblemStore
from raidar.targets.job import JobTarget


def load_problems(plan: ExperimentPlan, store: ProblemStore) -> dict[int, ImportedProblem]:
    """Every planned problem, content-verified and verifier-validated (F13)."""
    loaded = {}
    for target in plan.problems:
        problem = store.load(target.ref)
        store.require_validated(target.ref, problem.manifest)
        loaded[target.index] = problem
    return loaded


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
    manifest = start_job(ctx)
    plan = ctx.plan
    finished: list[TrialRecord] = []
    sealed: dict[str, str] = {}
    guard = ctx.target.guard

    async def admit(trial: PlannedTrial) -> None:
        if guard is not None:
            await guard.admit(trial.trial_id)

    async def run_one(trial: PlannedTrial) -> TrialRecord:
        record = await run_trial(ctx, trial)
        trial_id = trial.trial_id
        sealed[trial_id] = await asyncio.to_thread(
            seal_trial,
            ctx.trials_dir / trial_id,
            ctx.job_dir / TRIAL_RECORDS_DIR / f"{trial_id}.json",
            ctx.job_dir,
        )
        finished.append(record)
        return record

    try:
        return await run_in_order(
            plan.selected_trials(),
            run_one,
            concurrency=ctx.target.selection.trial.effective_concurrency(plan.concurrency),
            admit=admit,
        )
    except BaseException as error:
        manifest["aborted"] = type(error).__name__
        raise
    finally:
        manifest |= {
            "finished_at": datetime.now(UTC).isoformat(),
            "summary": _summary(finished),
            "records_manifests": dict(sorted(sealed.items())),
        }
        write_json(ctx.job_dir / JOB_MANIFEST, manifest)


async def run_job(
    plan: ExperimentPlan,
    problems: Mapping[int, ImportedProblem],
    *,
    job_dir: Path,
    job_id: str,
    target: JobTarget,
    admission: Mapping[str, Any] | None = None,
) -> list[TrialRecord]:
    """``execute`` a new job context for ``plan`` (what ``raidar run`` calls)."""
    ctx = JobContext(
        plan=plan,
        job_dir=job_dir,
        job_id=job_id,
        problems=problems,
        target=target,
        admission=admission,
    )
    return await execute(ctx)
