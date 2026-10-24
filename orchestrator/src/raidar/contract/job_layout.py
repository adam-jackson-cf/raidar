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
    detach/                  a detached launch only (``raidar.engine.job.detach``): the
                             launch record, the supervisor's pid, the job's log and
                             its exit record; written before the job starts

Inside each Harbor trial directory the verifier keeps its v1 evidence names
(``raidar-oracle/steps/<step>/record.json``, reward key ``oracle_leak``,
verifier kwarg ``oracle_bundle``): they are wire names that released scorers
and ``assess --regrade`` read identically for every job.
"""

from __future__ import annotations

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
