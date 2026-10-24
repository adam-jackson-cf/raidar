"""Experiment-epoch inputs: job directories, their experiment locks and trial records.

A job directory (``raidar run``) holds ``job.json``, ``plan.json``,
``assessment-config.yaml``, ``trials/`` and ``trials/``. The experiment
spec (agents, conditions, factors, headline metric) lives in the lock the run
was planned from; ``job.json`` names it by ``lock_sha256``. The lock is looked
up among the locks given explicitly, those beside the run's experiment file
(the current lock, whose assessment settings apply after a relock that kept
``lock_sha256``) and the run's own ``job.lock.json`` (the lock it executed
under); it must be a sealed lock whose ``lock_sha256`` (its execution digest)
matches. A run whose lock cannot be found is refused, never projected from
guesses; a lock file that cannot be parsed is skipped with a warning.

The assessment settings Assessments are looked up under are the lock's
(``execution.assessment.lock_rescore_config``), the same ones ``raidar assess
--rescore`` uses by default.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.contract.canonical import digest_json
from raidar.contract.job_layout import JOB_KIND, JOB_MANIFEST, PLAN_FILE, TRIAL_RECORDS_DIR
from raidar.design.experiment.lock import LOCK_SUFFIX, lock_integrity_issues
from raidar.design.experiment.schema import ExperimentSpec
from raidar.engine.job.assessment import lock_errata, rescore_config
from raidar.engine.trial.models import TrialRecord
from raidar.grading.errata import ErrataRef
from raidar.scoring.framework.rescore_config import RescoreConfig

SYNTHETIC_MARKER = "synthetic"


class JobSourceError(ValueError):
    """A job directory cannot be projected as stored."""


@dataclass(frozen=True)
class ExperimentLock:
    """The parts of an experiment lock the review needs."""

    sha256: str
    spec: ExperimentSpec
    metric: str | None
    conditions: Mapping[str, Any]
    problems: tuple[Mapping[str, Any], ...]
    path: Path | None = None
    errata: Mapping[str, ErrataRef] = field(default_factory=dict)
    """The problem errata the lock pins; correctness Assessments are keyed with them."""

    def condition_digest(self, condition: str) -> str:
        """The condition's locked contents, independent of the harness an arm compiles it for."""
        return digest_json(self.conditions.get(condition, []))


@dataclass(frozen=True)
class JobSource:
    job_dir: Path
    manifest: Mapping[str, Any]
    plan: Mapping[str, Any]
    lock: ExperimentLock
    assessment: RescoreConfig
    records: tuple[TrialRecord, ...]
    assessment_path: Path | None = None
    """The lock whose assessment settings ``assessment`` is (for the rescore hint)."""

    @property
    def experiment(self) -> str:
        return str(self.manifest["experiment"])

    @property
    def job_id(self) -> str:
        return str(self.manifest["job_id"])

    @property
    def synthetic(self) -> bool:
        return self.manifest.get(SYNTHETIC_MARKER) is True

    @property
    def agent_override(self) -> str | None:
        options = self.plan.get("options")
        value = options.get("agent_override") if isinstance(options, Mapping) else None
        return value if isinstance(value, str) else None


def _read_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobSourceError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise JobSourceError(f"{path} is not a JSON object")
    return loaded


def _is_job_dir(path: Path) -> bool:
    manifest = path / JOB_MANIFEST
    if not manifest.is_file():
        return False
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("kind") == JOB_KIND
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def discover_job_dirs(roots: Iterable[Path]) -> list[Path]:
    """Job directories at, one below, or two below each root (``<experiment>/<job>``)."""
    found: set[Path] = set()
    for root in roots:
        candidates = [root, *root.glob("*"), *root.glob("*/*")]
        found.update(path.resolve() for path in candidates if path.is_dir() and _is_job_dir(path))
    return sorted(found)


def parse_lock(document: Mapping[str, Any], origin: Path) -> ExperimentLock:
    """The lock, refused unless it is a sealed lock (as ``raidar analyze`` reads it)."""
    issues = lock_integrity_issues(document)
    if issues:
        raise JobSourceError(f"{origin}: not a valid lock ({'; '.join(issues)})")
    try:
        spec = ExperimentSpec.model_validate(document["spec"])
        execution = document["execution"]
        headline = document["assessment"].get("metric")
        return ExperimentLock(
            sha256=str(document["lock_sha256"]),
            spec=spec,
            metric=str(headline["ref"]) if isinstance(headline, Mapping) else None,
            conditions=dict(execution.get("conditions") or {}),
            problems=tuple(execution.get("problems") or ()),
            path=origin,
            errata=lock_errata(document),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise JobSourceError(f"{origin} is not an experiment lock: {exc}") from exc


class LockIndex:
    """Experiment locks by ``lock_sha256``: explicit files plus those beside experiment files."""

    def __init__(self, lock_files: Sequence[Path] = ()) -> None:
        self._locks: dict[str, ExperimentLock] = {}
        self._scanned: set[Path] = set()
        for path in lock_files:
            self._add(path)

    def _add(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self._scanned:
            return
        self._scanned.add(resolved)
        lock = parse_lock(_read_object(resolved), resolved)
        self._locks.setdefault(lock.sha256, lock)

    def _scan(self, directory: Path) -> None:
        """Every lock in ``directory``; one that cannot be parsed is skipped, not fatal."""
        for path in sorted(directory.glob(f"*{LOCK_SUFFIX}")):
            try:
                self._add(path)
            except JobSourceError as exc:
                print(f"Skipping unusable lock {path}: {exc}", file=sys.stderr)

    def find(self, sha256: str, *, job_dir: Path, experiment_file: str | None) -> ExperimentLock:
        if sha256 not in self._locks:
            # The experiment's current lock first: its assessment settings win.
            if experiment_file:
                self._scan(Path(experiment_file).parent)
            self._scan(job_dir)
        lock = self._locks.get(sha256)
        if lock is None:
            raise JobSourceError(
                f"{job_dir}: no experiment lock with lock_sha256 {sha256} beside the run or its "
                "experiment file; pass it with --lock"
            )
        return lock


def _records(job_dir: Path) -> tuple[TrialRecord, ...]:
    records = []
    for path in sorted((job_dir / TRIAL_RECORDS_DIR).glob("*.json")):
        try:
            records.append(TrialRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError) as exc:
            raise JobSourceError(f"invalid trial record {path}: {exc}") from exc
    return tuple(records)


def load_job(job_dir: Path, locks: LockIndex) -> JobSource:
    manifest = _read_object(job_dir / JOB_MANIFEST)
    lock = locks.find(
        str(manifest.get("lock_sha256")),
        job_dir=job_dir,
        experiment_file=manifest.get("experiment_file"),
    )
    return JobSource(
        job_dir=job_dir,
        manifest=manifest,
        plan=_read_object(job_dir / PLAN_FILE),
        lock=lock,
        assessment=rescore_config(lock.spec.assessment, lock.spec.protocol, errata=lock.errata),
        records=_records(job_dir),
        assessment_path=lock.path,
    )
