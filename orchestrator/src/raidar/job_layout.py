"""Job directory layout (schema v2 vocabulary: a *job* is one execution of a locked experiment).

A job directory (``<jobs root>/<experiment id>/<job id>/``) holds::

    job.json                 job manifest: lock, options, selected trials, outcome summary
    job.lock.json            the lock the job executed under (found by review/analysis
                             even after the experiment's own lock is relocked)
    plan.json                the full plan with every trial's hashes (D1)
    assessment-config.yaml   locked assessor settings for ``raidar assess --rescore``
    tasks/<trial>/           the materialised Harbor task of each trial
    trials/<trial>/          Harbor's trial directory (steps/, raidar-oracle/, result.json)
    trial-records/<trial>.json  TrialRecord: step results, validity, N1 phases
    detach/                  a detached launch only (``raidar.execution.detach``): the
                             launch record, the supervisor's pid, the job's log and
                             its exit record; written before the job starts

Inside each Harbor trial directory the verifier keeps its v1 evidence names
(``raidar-oracle/steps/<step>/record.json``, reward key ``oracle_leak``,
verifier kwarg ``oracle_bundle``): they are wire names that released scorers
and ``assess --regrade`` read identically for every job, old or new.

A job written before schema v2 (``run.json``, ``run.lock.json``,
``trajectories/``) is a *legacy job*: readers refuse it with a pointer to
``raidar migrate-job``, which moves it onto the v2 layout with provenance.
"""

from __future__ import annotations

from pathlib import Path

JOB_KIND = "raidar-job"
JOB_MANIFEST = "job.json"
JOB_LOCK_FILE = "job.lock.json"
PLAN_FILE = "plan.json"
TASKS_DIR = "tasks"
TRIALS_DIR = "trials"
TRIAL_RECORDS_DIR = "trial-records"
TRIAL_RECORD_GLOB = f"{TRIAL_RECORDS_DIR}/*.json"
DETACH_DIR = "detach"
"""A detached launch's files; the only entry a job directory may hold before its job starts."""

LEGACY_RUN_MANIFEST = "run.json"
LEGACY_RUN_LOCK_FILE = "run.lock.json"
LEGACY_TRAJECTORIES_DIR = "trajectories"
ARCHIVED_LEGACY_DIR = "legacy-v1"
"""Where ``raidar migrate-job`` keeps a migrated job's legacy files, byte for byte."""


class LegacyJobError(ValueError):
    """A job directory still in the pre-v2 layout."""


def is_legacy_job(path: Path) -> bool:
    """A pre-v2 job directory (never the archived ``legacy-v1/`` copy inside a migrated job)."""
    if path.name == ARCHIVED_LEGACY_DIR and (path.parent / JOB_MANIFEST).is_file():
        return False
    return (path / LEGACY_RUN_MANIFEST).is_file() and not (path / JOB_MANIFEST).is_file()


def legacy_job_message(path: Path) -> str:
    return (
        f"{path} is a legacy (pre-schema-v2) job: it has {LEGACY_RUN_MANIFEST} and "
        f"{LEGACY_TRAJECTORIES_DIR}/; move it onto the v2 layout with "
        f"`make experiment-migrate-job JOB={path} LOCK=<the experiment's v2 lock>`"
    )


def require_v2_job(path: Path) -> None:
    """Raise ``LegacyJobError`` for a legacy job directory."""
    if is_legacy_job(path):
        raise LegacyJobError(legacy_job_message(path))
