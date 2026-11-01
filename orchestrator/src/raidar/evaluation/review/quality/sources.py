"""Quality trial sources, locked assessment settings and exact Assessment lookup.

- **Trials** come from job directories
  (``<jobs_root>/<experiment>/<job>/trial-records/<trial>.json``).
- **Assessments** come from one Assessment store, under the exact key derived
  from the selected lock's settings, current scorer identity, tool lock and
  stored trial inputs, just as analysis and the main review projection do.
- The **lock** the run was executed under supplies the baseline arm and the
  headline metric, as the analysis reads them. Candidates, in order: locks
  given with ``--lock`` and the lock next to the ``job.json`` ``experiment_file``
  (``default_lock_path``). A candidate is accepted only when it is a sealed
  lock (``read_lock``) whose ``lock_sha256`` (the digest of its execution
  section, unchanged by scorer or threshold relocks) is the run's. The live
  experiment YAML is never read. Without a matching lock the baseline is
  unknown and the headline metric is the default by K, and the projection
  says why (``ExperimentInfo.note``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from raidar.contract.job_layout import JOB_KIND, JOB_MANIFEST, PLAN_FILE, TRIAL_RECORDS_DIR
from raidar.contract.trial import TrialRecord
from raidar.design.experiment.lock import default_lock_path
from raidar.engine.job.assessment import lock_rescore_config
from raidar.evaluation.analysis.sources import (
    AnalysisInputError,
    JobDir,
    read_lock,
    read_trial_records,
)
from raidar.evaluation.review.projection.assessments import AssessmentLookup, TrialAssessments
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.framework.rescore_config import RescoreConfig
from raidar.scoring.framework.store import Assessment, AssessmentStore

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class ExperimentInfo:
    """What the run's lock says, or why it could not be read (``note``)."""

    lock_sha256: str | None
    baseline_arm: str | None
    metric: str | None
    note: str | None = None
    analysis: Mapping[str, Any] | None = None
    """The lock's ``spec.analysis`` section (verdict thresholds), when the lock was read."""
    assessment: RescoreConfig | None = None
    scorers: tuple[str, ...] = ()
    lock_path: Path | None = None


@dataclass(frozen=True)
class TrialSource:
    jobs_root: Path
    job_dir: Path
    job_id: str
    finished_at: str
    record: TrialRecord
    experiment: ExperimentInfo
    job: JobDir | None = None

    @property
    def trial_dir(self) -> Path:
        stored = Path(self.record.trial_dir)
        return stored if stored.is_absolute() else self.job_dir / stored

    @property
    def review_id(self) -> str:
        """Review id ``<experiment>.<job>.<trial>`` (server-safe characters only)."""
        record = self.record
        return _UNSAFE_ID.sub("-", f"{record.experiment}.{self.job_id}.{record.trial.trial_id}")

    @property
    def truncation(self) -> str | None:
        """Why this trial is a development job (``max_checkpoints``), if it is one."""
        if self.record.truncated_to is None:
            return None
        return f"truncated to the first {self.record.truncated_to} checkpoint(s) (max_checkpoints)"

    def portable_path(self, path: Path) -> str:
        """``path`` relative to the jobs root, so the projection is not machine-specific."""
        return Path(os.path.relpath(path, self.jobs_root)).as_posix()

    @property
    def trial_record(self) -> str:
        """The trial record as ``<jobs-root name>/<job dir>/trial-records/<trial>.json``.

        The run review (``review.projection``) shows the same file as its
        ``artifact_paths.run_json``; the surface joins a run to its Quality view
        on this path suffix, which stays portable (no machine-specific prefix).
        """
        relative = Path(os.path.relpath(self.job_dir, self.jobs_root))
        path = Path(self.jobs_root.name) / relative / TRIAL_RECORDS_DIR
        return (path / f"{self.record.trial.trial_id}.json").as_posix()

    def snapshot_digests(self) -> dict[str, str | None]:
        return {
            step_result.step: step_result.snapshot.digest if step_result.snapshot else None
            for step_result in self.record.step_results
        }


def _lock_facts(lock: dict[str, Any], executed: str, path: Path) -> ExperimentInfo:
    spec = lock.get("spec") or {}
    headline = (lock.get("assessment") or {}).get("metric") or {}
    analysis = spec.get("analysis") or {}
    return ExperimentInfo(
        lock_sha256=executed,
        baseline_arm=analysis.get("baseline_arm"),
        metric=headline.get("ref") if isinstance(headline, dict) else None,
        analysis=analysis,
        assessment=lock_rescore_config(lock),
        scorers=tuple((spec.get("assessment") or {}).get("scorers", ())),
        lock_path=path,
    )


def _lock_candidates(run: Mapping[str, Any], locks: Sequence[Path]) -> list[Path]:
    candidates = list(locks)
    experiment_file = run.get("experiment_file")
    if isinstance(experiment_file, str):
        candidates.append(default_lock_path(Path(experiment_file)))
    return candidates


def _experiment_info(run: Mapping[str, Any], locks: Sequence[Path] = ()) -> ExperimentInfo:
    executed = str(run.get("lock_sha256") or "") or None
    if executed is None:
        return ExperimentInfo(executed, None, None, "job.json names no lock_sha256")
    reasons: list[str] = []
    for path in _lock_candidates(run, locks):
        if not path.is_file():
            reasons.append(f"no {path.name} found")
            continue
        try:
            lock = read_lock(path)
        except AnalysisInputError as exc:
            reasons.append(str(exc).replace(str(path), path.name))
            continue
        if lock.get("lock_sha256") == executed:
            return _lock_facts(lock, executed, path)
        found = str(lock.get("lock_sha256"))[:12]
        reasons.append(
            f"{path.name} is now {found}, but the run was executed under {executed[:12]}"
        )
    detail = "; ".join(reasons) or "no candidate lock (pass --lock)"
    return ExperimentInfo(executed, None, None, f"lock unavailable: {detail}")


def _job_dirs(jobs_root: Path) -> Iterator[Path]:
    for manifest in sorted(jobs_root.rglob(JOB_MANIFEST)):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == JOB_KIND:
            yield manifest.parent


def job_trials(jobs_root: Path, job: JobDir, locks: Sequence[Path] = ()) -> list[TrialSource]:
    """The trials of one run read from ``job`` (its manifest names the lock it ran under)."""
    run = job.manifest
    experiment = _experiment_info(run, locks)
    return [
        TrialSource(
            jobs_root=jobs_root,
            job_dir=job.path,
            job_id=str(run.get("job_id") or job.path.name),
            finished_at=str(run.get("finished_at") or ""),
            record=record,
            experiment=experiment,
            job=job,
        )
        for record in job.records
    ]


def discover_trials(jobs_root: Path, locks: Sequence[Path] = ()) -> list[TrialSource]:
    """Every trial of every Raidar run under ``jobs_root``, in path order."""
    trials: list[TrialSource] = []
    for job_dir in _job_dirs(jobs_root):
        plan_path = job_dir / PLAN_FILE
        job = JobDir(
            path=job_dir,
            manifest=json.loads((job_dir / JOB_MANIFEST).read_text(encoding="utf-8")),
            plan=json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {},
            records=read_trial_records(job_dir),
        )
        trials.extend(job_trials(jobs_root, job, locks))
    return trials


def _assessment_steps(assessment: Assessment) -> dict[str, str | None]:
    steps = assessment.inputs.get("steps") or []
    return {str(step["step"]): step.get("snapshot") for step in steps}


class TrialLookup(Protocol):
    """What ``StoreIndex`` asks a store: a trial's stored Assessments by scorer."""

    def for_trial(
        self,
        trial_dir: Path,
        scorer_refs: Sequence[str],
        settings: RescoreConfig,
        *,
        config_path: Path | None = None,
    ) -> TrialAssessments: ...


class StoreIndex:
    """Assessments of one store, found under the selected lock's exact keys."""

    def __init__(self, lookup: TrialLookup) -> None:
        self._lookup = lookup
        self._trials: dict[str, TrialAssessments] = {}

    @classmethod
    def open(cls, root: Path) -> StoreIndex:
        return cls(AssessmentLookup([AssessmentStore(root)], default_scorer_registry()))

    def find(self, scorer_ref: str, trial: TrialSource) -> Assessment | None:
        experiment = trial.experiment
        if experiment.assessment is None or scorer_ref not in experiment.scorers:
            return None
        if trial.review_id not in self._trials:
            self._trials[trial.review_id] = self._lookup.for_trial(
                trial.trial_dir,
                experiment.scorers,
                experiment.assessment,
                config_path=experiment.lock_path,
            )
        found = self._trials[trial.review_id].get(scorer_ref)
        if found is None or not _matches(_assessment_steps(found), trial.snapshot_digests()):
            return None
        return found


def _matches(stored: dict[str, str | None], digests: dict[str, str | None]) -> bool:
    if not any(digests.values()):
        return False
    if any(digest and stored.get(step) != digest for step, digest in digests.items()):
        return False
    return all(
        snapshot is None or digests.get(step) == snapshot for step, snapshot in stored.items()
    )
