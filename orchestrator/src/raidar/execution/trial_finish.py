"""Finish one trial: target review, run-health audit, then the trial record.

After classification a trial passes the target guard (an invalid trial that lost its Docker
target is marked and reaped), is audited (``health.json`` beside the trial and provisional
fault candidates in the job directory; each health violation becomes a validity check, see
``raidar.execution.collect.health_validity``) and only then is its record written, so the
record's validity already includes the audit.
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

from raidar.core.job_layout import TRIAL_RECORDS_DIR
from raidar.execution.collect import health_validity
from raidar.execution.health.audit import audit_trial
from raidar.execution.health.fault_candidates import append_fault_candidates
from raidar.execution.health.model import write_health
from raidar.execution.job_context import JobContext
from raidar.execution.models import ExecutionValidity, PlannedTrial, TrialRecord, ValidityCheck
from raidar.execution.perf.contention import read_manifest
from raidar.execution.trial_record import write_record

HEALTH_ERROR_FILE = "raidar-health-error.txt"


def audit_and_write(
    job_dir: Path, trial_dir: Path, record: TrialRecord
) -> tuple[ValidityCheck, ...]:
    """``health.json`` and the fault candidates; the validity checks its violations map to."""
    try:
        report = audit_trial(job_dir, trial_dir, record, read_manifest(job_dir))
        write_health(trial_dir, report)
        append_fault_candidates(job_dir, report)
        return health_validity(report)
    except Exception as error:  # noqa: BLE001 - an unauditable trial is invalid, never lost
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        (trial_dir / HEALTH_ERROR_FILE).write_text(trace)
        detail = f"health audit raised {type(error).__name__}: {error}"
        return (ValidityCheck(name="health_audit", passed=False, detail=detail),)


async def finish_trial(ctx: JobContext, trial: PlannedTrial, record: TrialRecord) -> TrialRecord:
    """Review, audit and write ``record``; returns the record as written."""
    trial_dir = ctx.trials_dir / trial.trial_id
    if ctx.target.guard is not None:
        record = await ctx.target.guard.review(
            record, trial_dir=trial_dir, trials_dir=ctx.trials_dir
        )
    trial_dir.mkdir(parents=True, exist_ok=True)
    checks = await asyncio.to_thread(audit_and_write, ctx.job_dir, trial_dir, record)
    validity = ExecutionValidity(checks=(*record.validity.checks, *checks))
    audited = record.model_copy(update={"validity": validity})
    return write_record(ctx.job_dir / TRIAL_RECORDS_DIR / f"{trial.trial_id}.json", audited)
