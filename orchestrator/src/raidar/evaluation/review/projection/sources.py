"""Experiment-epoch inputs: job directories, their experiment locks and trial records.

A job directory (``raidar run``) holds ``job.json``, ``plan.json``,
``assessment-config.yaml``, ``trials/`` and ``trial-records/``. The experiment
spec (agents, conditions, factors, headline metric) lives in the lock the run was
planned from; ``job.json`` names it by ``lock_sha256``. The lock is one given
explicitly or the one beside the run's experiment file (the current lock, whose
assessment settings apply after a relock that kept ``lock_sha256``); it must be a
sealed lock whose ``lock_sha256`` (its execution digest) matches. A run whose lock
cannot be found is refused, never projected from guesses.

The assessment settings Assessments are looked up under are the lock's
(``execution.assessment.lock_rescore_config``), the same ones ``raidar assess
--rescore`` uses by default.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.contract.canonical import digest_json
from raidar.contract.job_layout import JOB_KIND, JOB_MANIFEST, PLAN_FILE
from raidar.contract.trial import TrialRecord
from raidar.design.experiment.lock import default_lock_path
from raidar.design.experiment.schema import ExperimentSpec
from raidar.engine.job.assessment import lock_errata, rescore_config
from raidar.evaluation.analysis.sources import (
    AnalysisInputError,
    read_json_object,
    read_lock,
    read_trial_records,
)
from raidar.grading.errata import ErrataRef
from raidar.scoring.framework.rescore_config import RescoreConfig

SYNTHETIC_MARKER = "synthetic"


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


def experiment_lock(path: Path) -> ExperimentLock:
    """The sealed lock at ``path`` (as ``raidar analyze`` reads it), with what the review needs."""
    document = read_lock(path)
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
            path=path,
            errata=lock_errata(document),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise AnalysisInputError(f"{path} is not an experiment lock: {exc}") from exc


class LockIndex:
    """The lock of a job: one given explicitly, else the lock beside the job's experiment file."""

    def __init__(self, lock_files: Sequence[Path] = ()) -> None:
        self._given: dict[str, ExperimentLock] = {}
        self._beside: dict[Path, ExperimentLock] = {}
        for path in lock_files:
            lock = experiment_lock(path.resolve())
            self._given.setdefault(lock.sha256, lock)

    def find(self, sha256: str, *, job_dir: Path, experiment_file: str | None) -> ExperimentLock:
        lock = self._given.get(sha256)
        if lock is None and experiment_file:
            path = default_lock_path(Path(experiment_file)).resolve()
            if path not in self._beside and path.is_file():
                self._beside[path] = experiment_lock(path)
            beside = self._beside.get(path)
            lock = beside if beside is not None and beside.sha256 == sha256 else None
        if lock is None:
            raise AnalysisInputError(
                f"{job_dir}: no experiment lock with lock_sha256 {sha256} given with --lock or "
                "beside the run's experiment file; pass it with --lock"
            )
        return lock


def load_job(job_dir: Path, locks: LockIndex) -> JobSource:
    manifest = read_json_object(job_dir / JOB_MANIFEST)
    lock = locks.find(
        str(manifest.get("lock_sha256")),
        job_dir=job_dir,
        experiment_file=manifest.get("experiment_file"),
    )
    return JobSource(
        job_dir=job_dir,
        manifest=manifest,
        plan=read_json_object(job_dir / PLAN_FILE),
        lock=lock,
        assessment=rescore_config(lock.spec.assessment, lock.spec.protocol, errata=lock.errata),
        records=read_trial_records(job_dir),
        assessment_path=lock.path,
    )
