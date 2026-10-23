"""Read-only access to executed jobs and their experiment lock.

A job directory holds ``job.json``, ``plan.json`` and ``trial-records/*.json``;
the lock is checked against its own digest and against every job. Several jobs
of one lock can be analysed together (for example attempts run on different
days). A trial id that appears in more than one job is resolved by the
replacement rule: a valid trial replaces an invalid one, and the replaced
ids are reported; two valid trials for one trial are refused.

Jobs executed with a development option are not arm evidence: a
``max_checkpoints`` truncation stops every trial early, and an
``agent_override`` replaces the arm's agent with a no-LLM Harbor agent. Such
jobs are refused unless the caller opts in, and jobs with different options
are never pooled (or swapped for each other by the replacement rule).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.analysis.outcomes import PoolingReport
from raidar.analysis.pooling import PoolingError, check_pooling
from raidar.analysis.report import PlannedTrial
from raidar.execution.models import TrialRecord
from raidar.spec.lock import default_lock_path, lock_integrity_issues


class AnalysisInputError(ValueError):
    """The jobs, lock or store cannot be analysed together."""


@dataclass(frozen=True)
class DevOptions:
    """The execution options that make a job's trials something other than arm data."""

    max_checkpoints: int | None = None
    agent_override: str | None = None

    @property
    def active(self) -> bool:
        return self.max_checkpoints is not None or self.agent_override is not None

    def describe(self) -> str:
        parts = []
        if self.max_checkpoints is not None:
            parts.append(f"truncated to the first {self.max_checkpoints} checkpoint(s)")
        if self.agent_override is not None:
            parts.append(f"agent replaced by the no-LLM override {self.agent_override!r}")
        return "; ".join(parts) or "no development options"


@dataclass(frozen=True)
class JobDir:
    path: Path
    manifest: Mapping[str, Any]
    plan: Mapping[str, Any]
    records: tuple[TrialRecord, ...]

    @property
    def job_id(self) -> str:
        return str(self.manifest.get("job_id", self.path.name))

    @property
    def options(self) -> DevOptions:
        declared = self.plan.get("options") or {}
        maximum, override = declared.get("max_checkpoints"), declared.get("agent_override")
        return DevOptions(
            max_checkpoints=int(maximum) if maximum is not None else None,
            agent_override=str(override) if override is not None else None,
        )

    def trial_dir(self, record: TrialRecord) -> Path:
        stored = Path(record.trial_dir)
        return stored if stored.is_absolute() else self.path / stored


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisInputError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AnalysisInputError(f"{path} is not a JSON object")
    return loaded


def read_job(job_dir: Path) -> JobDir:
    records = []
    for path in sorted((job_dir / "trial-records").glob("*.json")):
        try:
            records.append(TrialRecord.model_validate_json(path.read_bytes()))
        except ValidationError as exc:
            raise AnalysisInputError(f"invalid trial record {path}: {exc}") from exc
    return JobDir(
        path=job_dir,
        manifest=_read_json(job_dir / "job.json"),
        plan=_read_json(job_dir / "plan.json"),
        records=tuple(records),
    )


def read_lock(path: Path) -> dict[str, Any]:
    """The lock document, refused unless it is a sealed lock (``lock_integrity_issues``).

    Its ``lock_sha256`` is the digest of its execution section, the identity
    jobs record; scorer and threshold changes relock without changing it.
    """
    lock = _read_json(path)
    issues = lock_integrity_issues(lock)
    if issues:
        raise AnalysisInputError(f"{path}: not a valid lock ({'; '.join(issues)})")
    return lock


JOB_LOCK_FILE = "job.lock.json"


def default_lock(job: JobDir) -> Path:
    """The experiment's current lock when it still describes the job, else the job's copy.

    The current lock is preferred: a relock that changes only assessment or
    analysis settings keeps ``lock_sha256`` and its settings apply. A job's
    ``job.lock.json`` (the lock it executed under) is used when the
    experiment's lock is missing or now describes another experiment.
    """
    experiment_file = job.manifest.get("experiment_file")
    copy = job.path / JOB_LOCK_FILE
    if not isinstance(experiment_file, str):
        if copy.is_file():
            return copy
        raise AnalysisInputError(f"{job.path}/job.json names no experiment_file; pass --lock")
    current = default_lock_path(Path(experiment_file))
    if copy.is_file() and _lock_digest(current) != job.manifest.get("lock_sha256"):
        return copy
    return current


def _lock_digest(path: Path) -> object:
    try:
        return json.loads(path.read_text()).get("lock_sha256")
    except (OSError, ValueError, AttributeError):
        return None


def _record_options(record: TrialRecord) -> DevOptions:
    return DevOptions(max_checkpoints=record.truncated_to, agent_override=record.agent_override)


def job_options(jobs: Sequence[JobDir], *, allow_dev_jobs: bool) -> DevOptions:
    """The development options shared by every job and record, or a refusal.

    Records carry the options they were executed with; a record that disagrees
    with its job's plan is refused, as are jobs with different options. Jobs
    with any option active are refused unless ``allow_dev_jobs``.
    """
    for job in jobs:
        for record in job.records:
            if _record_options(record) != job.options:
                raise AnalysisInputError(
                    f"{job.path}: trial {record.trial.trial_id} was executed with "
                    f"{_record_options(record).describe()}, but the plan declares "
                    f"{job.options.describe()}"
                )
    options = {job.options for job in jobs}
    if len(options) != 1:
        described = "; ".join(f"{job.path}: {job.options.describe()}" for job in jobs)
        raise AnalysisInputError(
            f"the jobs were executed with different development options ({described}); "
            "analyse them apart"
        )
    (shared,) = options
    if shared.active and not allow_dev_jobs:
        raise AnalysisInputError(
            f"the jobs were executed with {shared.describe()}: their trials are not arm "
            "evidence. Pass --allow-dev-jobs to analyse them as a labelled development job"
        )
    return shared


def check_jobs(
    jobs: Sequence[JobDir], lock: Mapping[str, Any], pool_overrides: Sequence[str] = ()
) -> PoolingReport:
    """Refuse jobs of another lock or plan, then apply the pooling gate (``check_pooling``)."""
    for job in jobs:
        if job.manifest.get("lock_sha256") != lock["lock_sha256"]:
            raise AnalysisInputError(
                f"run {job.path} was executed under lock {job.manifest.get('lock_sha256')}, "
                f"not {lock['lock_sha256']}: the lock's execution section describes another "
                "experiment"
            )
    trial_sets = {tuple(t["trial_id"] for t in job.plan.get("trials", ())) for job in jobs}
    if len(trial_sets) != 1:
        raise AnalysisInputError("the jobs' plans list different trials; analyse them apart")
    try:
        return check_pooling(jobs, pool_overrides)
    except PoolingError as error:
        raise AnalysisInputError(str(error)) from error


def planned_trials(plan: Mapping[str, Any]) -> tuple[PlannedTrial, ...]:
    return tuple(
        PlannedTrial(
            trial_id=str(trial["trial_id"]),
            arm=str(trial["arm"]),
            attempt=int(trial["attempt"]),
            problem=str(trial["problem"]),
        )
        for trial in plan.get("trials", ())
    )


def select_records(
    jobs: Sequence[JobDir],
) -> tuple[dict[str, tuple[JobDir, TrialRecord]], tuple[str, ...]]:
    """One record per trial id under the replacement rule, plus the replaced ones."""
    chosen: dict[str, tuple[JobDir, TrialRecord]] = {}
    replaced: list[str] = []
    for job in jobs:
        for record in job.records:
            trial = record.trial.trial_id
            previous = chosen.get(trial)
            if previous is not None and previous[1].valid:
                if record.valid:
                    raise AnalysisInputError(
                        f"trial {trial} is valid in both {previous[0].path} and {job.path}"
                    )
                replaced.append(f"{job.job_id}/{trial}")
                continue
            if previous is not None:
                replaced.append(f"{previous[0].job_id}/{trial}")
            chosen[trial] = (job, record)
    return chosen, tuple(replaced)
