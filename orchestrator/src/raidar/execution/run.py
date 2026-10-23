"""Execute a plan (plan D2-D4): schedule the trials of a job and write the job directory.

Writes one job directory (``<jobs_root>/<experiment id>/<job id>/``); the
layout is ``raidar.job_layout``. One trial is ``raidar.execution.trial_run``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from raidar.execution.assessment import lock_errata, write_assessment_config
from raidar.execution.driver import run_in_order
from raidar.execution.models import PlannedTrial, TrialRecord
from raidar.execution.plan import ExperimentPlan
from raidar.execution.provenance import effective_limits, recipe_digests, runner_provenance
from raidar.execution.records_manifest import seal_trial
from raidar.execution.trial_run import JobContext, run_trial
from raidar.job_layout import (
    DETACH_DIR,
    JOB_KIND,
    JOB_LOCK_FILE,
    JOB_MANIFEST,
    PLAN_FILE,
    TRIAL_RECORDS_DIR,
)
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
        **ctx.target.manifest_fields(plan.concurrency),
        "runner": runner_provenance(),
        "recipes": recipe_digests(plan, ctx.problems),
        "effective_limits": effective_limits(plan),
        "concurrency": plan.concurrency,
        "selected": [trial.trial_id for trial in plan.selected_trials()],
        "partial": len(plan.selected) != len(plan.trials),
        "started_at": datetime.now(UTC).isoformat(),
    }
    _write_json(ctx.job_dir / JOB_MANIFEST, manifest)
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
        _write_json(ctx.job_dir / JOB_MANIFEST, manifest)


async def run_job(
    plan: ExperimentPlan,
    problems: Mapping[int, ImportedProblem],
    *,
    job_dir: Path,
    job_id: str,
    target: JobTarget,
) -> list[TrialRecord]:
    """``execute`` a new job context for ``plan`` (what ``raidar run`` calls)."""
    ctx = JobContext(plan=plan, job_dir=job_dir, job_id=job_id, problems=problems, target=target)
    return await execute(ctx)
