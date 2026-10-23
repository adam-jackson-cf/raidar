"""Inputs of the Quality projection: job directories, the Assessment store, the Jev audit.

- **Trials** come from WS-D job directories
  (``<jobs_root>/<experiment>/<job>/trial-records/<trial>.json``).
- **Assessments** come from one Assessment store. A trial's Assessment of a
  scorer is found by content, not by the store log's trial name (trial ids
  such as ``b00-p0-baseline`` repeat across runs): the Assessment's recorded
  inputs must name the trial's problem and exactly its stored snapshot
  digests, step by step. When several Assessments of one scorer match (a new
  config or scorer build), the one logged most recently wins.
- **Jev decisions** come from the store's audit (``<store>/jev-audit``). A
  decision names its trial by a free string (``raidar jev shadow-rubric
  --trial``). It attaches to a trial when that string is the trial's review id
  (``<experiment>.<job>.<trial>``) or ``<job>/<trial>``; a bare trial id
  attaches only when exactly one projected trial has it. Trial ids repeat
  across runs, so an ambiguous bare id attaches nowhere and every trial that
  shares it says so: a reviewer never labels a decision against another run.
- The **lock** the run was executed under supplies the baseline arm and the
  headline metric, as WS-J's analysis reads them. Candidates, in order: locks
  given with ``--lock``, the lock next to the ``job.json`` ``experiment_file``
  (``default_lock_path``), and the locks in the job directory (``job.lock.json``,
  written by ``raidar run``). A candidate is accepted only when it is a sealed
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
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.analysis.sources import AnalysisInputError, read_lock
from raidar.execution.models import TrialRecord
from raidar.jev.audit import AUDIT_DIR, AuditLog, DecisionRecord, LabelRecord
from raidar.jev.ledger import current_labels
from raidar.scorers.store import LOG_FILE, OBJECTS_DIR, Assessment
from raidar.spec.lock import LOCK_SUFFIX, default_lock_path

CODE_QUALITY_REF = "code-quality@1"
CORRECTNESS_REF = "correctness@1"
JOB_KIND = "raidar-job"
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


@dataclass(frozen=True)
class TrialSource:
    jobs_root: Path
    job_dir: Path
    job_id: str
    finished_at: str
    record: TrialRecord
    experiment: ExperimentInfo

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
        path = Path(self.jobs_root.name) / relative / "trial-records"
        return (path / f"{self.record.trial.trial_id}.json").as_posix()

    def snapshot_digests(self) -> dict[str, str | None]:
        return {
            step_result.step: step_result.snapshot.digest if step_result.snapshot else None
            for step_result in self.record.step_results
        }


def _lock_facts(lock: dict[str, Any], executed: str) -> ExperimentInfo:
    spec = lock.get("spec") or {}
    headline = (lock.get("assessment") or {}).get("metric") or {}
    analysis = spec.get("analysis") or {}
    return ExperimentInfo(
        lock_sha256=executed,
        baseline_arm=analysis.get("baseline_arm"),
        metric=headline.get("ref") if isinstance(headline, dict) else None,
        analysis=analysis,
    )


def _lock_candidates(run: dict[str, Any], job_dir: Path, locks: Sequence[Path]) -> list[Path]:
    candidates = list(locks)
    experiment_file = run.get("experiment_file")
    if isinstance(experiment_file, str):
        candidates.append(default_lock_path(Path(experiment_file)))
    candidates.extend(sorted(job_dir.glob(f"*{LOCK_SUFFIX}")))
    return candidates


def _experiment_info(
    run: dict[str, Any], job_dir: Path, locks: Sequence[Path] = ()
) -> ExperimentInfo:
    executed = str(run.get("lock_sha256") or "") or None
    if executed is None:
        return ExperimentInfo(executed, None, None, "job.json names no lock_sha256")
    reasons: list[str] = []
    for path in _lock_candidates(run, job_dir, locks):
        if not path.is_file():
            reasons.append(f"no {path.name} found")
            continue
        try:
            lock = read_lock(path)
        except AnalysisInputError as exc:
            reasons.append(str(exc).replace(str(path), path.name))
            continue
        if lock.get("lock_sha256") == executed:
            return _lock_facts(lock, executed)
        found = str(lock.get("lock_sha256"))[:12]
        reasons.append(
            f"{path.name} is now {found}, but the run was executed under {executed[:12]}"
        )
    detail = "; ".join(reasons) or "no candidate lock (pass --lock)"
    return ExperimentInfo(executed, None, None, f"lock unavailable: {detail}")


def _job_dirs(jobs_root: Path) -> Iterator[Path]:
    for manifest in sorted(jobs_root.rglob("job.json")):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == JOB_KIND:
            yield manifest.parent


def discover_trials(jobs_root: Path, locks: Sequence[Path] = ()) -> list[TrialSource]:
    """Every trial of every Raidar run under ``jobs_root``, in path order."""
    trials: list[TrialSource] = []
    for job_dir in _job_dirs(jobs_root):
        run = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        experiment = _experiment_info(run, job_dir, locks)
        for path in sorted((job_dir / "trial-records").glob("*.json")):
            record = TrialRecord.model_validate_json(path.read_bytes())
            trials.append(
                TrialSource(
                    jobs_root=jobs_root,
                    job_dir=job_dir,
                    job_id=str(run.get("job_id") or job_dir.name),
                    finished_at=str(run.get("finished_at") or ""),
                    record=record,
                    experiment=experiment,
                )
            )
    return trials


def _assessment_steps(assessment: Assessment) -> dict[str, str | None]:
    steps = assessment.inputs.get("steps") or []
    return {str(step["step"]): step.get("snapshot") for step in steps}


@dataclass
class StoreIndex:
    """Assessments of one store, looked up by scorer and trial content."""

    root: Path
    _by_scorer: dict[str, list[tuple[int, Assessment]]] = field(default_factory=dict)

    @classmethod
    def open(cls, root: Path) -> StoreIndex:
        recency: dict[str, int] = {}
        log = root / LOG_FILE
        if log.is_file():
            for position, line in enumerate(log.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    recency[json.loads(line)["key"]] = position
        by_scorer: dict[str, list[tuple[int, Assessment]]] = defaultdict(list)
        for path in sorted((root / OBJECTS_DIR).glob("*/*.json")):
            assessment = Assessment.model_validate_json(path.read_bytes())
            by_scorer[assessment.scorer.ref].append((recency.get(assessment.key, -1), assessment))
        return cls(root=root, _by_scorer=dict(by_scorer))

    def find(self, scorer_ref: str, trial: TrialSource) -> Assessment | None:
        digests = trial.snapshot_digests()
        problem = trial.record.trial.problem
        matches = [
            (recency, assessment.key, assessment)
            for recency, assessment in self._by_scorer.get(scorer_ref, [])
            if assessment.inputs.get("problem") == problem
            and _matches(_assessment_steps(assessment), digests)
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: (item[0], item[1]))[2]


def _matches(stored: dict[str, str | None], digests: dict[str, str | None]) -> bool:
    if not any(digests.values()):
        return False
    if any(digest and stored.get(step) != digest for step, digest in digests.items()):
        return False
    return all(
        snapshot is None or digests.get(step) == snapshot for step, snapshot in stored.items()
    )


@dataclass(frozen=True)
class JevAttachment:
    """The decisions attached to one trial, and why others naming it were not."""

    decisions: tuple[DecisionRecord, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class JevAudit:
    decisions: dict[str, list[DecisionRecord]]
    labels: dict[str, LabelRecord]

    @classmethod
    def open(cls, store_root: Path) -> JevAudit:
        audit = AuditLog.for_store(store_root)
        root = store_root / AUDIT_DIR
        by_trial: dict[str, list[DecisionRecord]] = defaultdict(list)
        labels: dict[str, LabelRecord] = {}
        policies = (
            sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []
        )
        for policy in policies:
            for record in audit.decisions(policy):
                if record.trial is not None:
                    by_trial[record.trial].append(record)
            labels.update(current_labels(audit, policy))
        return cls(decisions=dict(by_trial), labels=labels)

    def attach(self, trials: list[TrialSource]) -> dict[str, JevAttachment]:
        """Decisions per trial review id, under the unambiguous-name rule above."""
        by_bare: dict[str, list[TrialSource]] = defaultdict(list)
        for trial in trials:
            by_bare[trial.record.trial.trial_id].append(trial)
        attached: dict[str, JevAttachment] = {}
        for trial in trials:
            bare = trial.record.trial.trial_id
            names = (trial.review_id, f"{trial.job_id}/{bare}")
            decisions = [record for name in names for record in self.decisions.get(name, [])]
            unresolved = self.decisions.get(bare, [])
            note = None
            if len(by_bare[bare]) == 1:
                decisions.extend(unresolved)
            elif unresolved:
                note = (
                    f"{len(unresolved)} Jev decision(s) name trial {bare!r}, which "
                    f"{len(by_bare[bare])} runs share; not attached. Record them with "
                    f"--trial {trial.review_id} to review them here"
                )
            attached[trial.review_id] = JevAttachment(decisions=tuple(decisions), note=note)
        return attached
