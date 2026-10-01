"""Experiment-epoch inputs: run directories, their experiment locks and trajectory records.

A run directory (``raidar run``) holds ``run.json``, ``plan.json``,
``assessment-config.yaml``, ``trials/`` and ``trajectories/``. The experiment
spec (agents, conditions, factors, headline policy) lives in the lock the run
was planned from; ``run.json`` names it by ``lock_sha256``. The lock is looked
up among the locks given explicitly, those beside the run's experiment file
(the current lock, whose assessment settings apply after a relock that kept
``lock_sha256``) and the run's own ``run.lock.json`` (the lock it executed
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.execution.assessment import rescore_config
from raidar.execution.models import TrajectoryRecord
from raidar.scorers.rescore_config import RescoreConfig
from raidar.spec.canonical import digest_json
from raidar.spec.lock import LOCK_SUFFIX, lock_integrity_issues
from raidar.spec.schema import ExperimentSpec

RUN_MANIFEST = "run.json"
RUN_KIND = "raidar-run"
SYNTHETIC_MARKER = "synthetic"


class RunSourceError(ValueError):
    """A run directory cannot be projected as stored."""


@dataclass(frozen=True)
class ExperimentLock:
    """The parts of an experiment lock the review needs."""

    sha256: str
    spec: ExperimentSpec
    headline_policy: str | None
    conditions: Mapping[str, Any]
    problems: tuple[Mapping[str, Any], ...]
    path: Path | None = None

    def condition_digest(self, condition: str) -> str:
        """The condition's locked contents, independent of the harness an arm compiles it for."""
        return digest_json(self.conditions.get(condition, []))


@dataclass(frozen=True)
class RunSource:
    run_dir: Path
    manifest: Mapping[str, Any]
    plan: Mapping[str, Any]
    lock: ExperimentLock
    assessment: RescoreConfig
    records: tuple[TrajectoryRecord, ...]
    assessment_path: Path | None = None
    """The lock whose assessment settings ``assessment`` is (for the rescore hint)."""

    @property
    def experiment(self) -> str:
        return str(self.manifest["experiment"])

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])

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
        raise RunSourceError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise RunSourceError(f"{path} is not a JSON object")
    return loaded


def _is_run_dir(path: Path) -> bool:
    manifest = path / RUN_MANIFEST
    if not manifest.is_file():
        return False
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("kind") == RUN_KIND
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def discover_run_dirs(roots: Iterable[Path]) -> list[Path]:
    """Run directories at, one below, or two below each root (``<experiment>/<run>``)."""
    found: set[Path] = set()
    for root in roots:
        candidates = [root, *root.glob("*"), *root.glob("*/*")]
        found.update(path.resolve() for path in candidates if path.is_dir() and _is_run_dir(path))
    return sorted(found)


def parse_lock(document: Mapping[str, Any], origin: Path) -> ExperimentLock:
    """The lock, refused unless it is a sealed lock (as ``raidar analyze`` reads it)."""
    issues = lock_integrity_issues(document)
    if issues:
        raise RunSourceError(f"{origin}: not a valid lock ({'; '.join(issues)})")
    try:
        spec = ExperimentSpec.model_validate(document["spec"])
        execution = document["execution"]
        headline = document["assessment"].get("headline_policy")
        return ExperimentLock(
            sha256=str(document["lock_sha256"]),
            spec=spec,
            headline_policy=str(headline["ref"]) if isinstance(headline, Mapping) else None,
            conditions=dict(execution.get("conditions") or {}),
            problems=tuple(execution.get("problems") or ()),
            path=origin,
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise RunSourceError(f"{origin} is not an experiment lock: {exc}") from exc


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
            except RunSourceError as exc:
                print(f"Skipping unusable lock {path}: {exc}", file=sys.stderr)

    def find(self, sha256: str, *, run_dir: Path, experiment_file: str | None) -> ExperimentLock:
        if sha256 not in self._locks:
            # The experiment's current lock first: its assessment settings win.
            if experiment_file:
                self._scan(Path(experiment_file).parent)
            self._scan(run_dir)
        lock = self._locks.get(sha256)
        if lock is None:
            raise RunSourceError(
                f"{run_dir}: no experiment lock with lock_sha256 {sha256} beside the run or its "
                "experiment file; pass it with --lock"
            )
        return lock


def _records(run_dir: Path) -> tuple[TrajectoryRecord, ...]:
    records = []
    for path in sorted((run_dir / "trajectories").glob("*.json")):
        try:
            records.append(TrajectoryRecord.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValidationError) as exc:
            raise RunSourceError(f"invalid trajectory record {path}: {exc}") from exc
    return tuple(records)


def load_run(run_dir: Path, locks: LockIndex) -> RunSource:
    manifest = _read_object(run_dir / RUN_MANIFEST)
    lock = locks.find(
        str(manifest.get("lock_sha256")),
        run_dir=run_dir,
        experiment_file=manifest.get("experiment_file"),
    )
    return RunSource(
        run_dir=run_dir,
        manifest=manifest,
        plan=_read_object(run_dir / "plan.json"),
        lock=lock,
        assessment=rescore_config(lock.spec.assessment, lock.spec.protocol),
        records=_records(run_dir),
        assessment_path=lock.path,
    )
