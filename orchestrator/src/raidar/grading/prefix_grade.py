"""Offline grades of the workspaces fix rounds started from (guardrail-gate@3 pre-fix snapshots).

``guardrail-gate@3`` snapshots ``/app`` before every fix session, with the
verifier's own artifact mechanism and excludes, under the step's agent logs
(``guardrail-gate/pre_fix_<round>/app``) and records each snapshot's
``tree_digest`` in the gate record. This module grades those snapshots
offline, beside the step's final snapshot:

- ``grade_pre_fix_path`` (run by ``raidar assess --rescore`` when a locked
  scorer reads pre-fix grades, ``gate-effort@10``): every pre-fix snapshot of
  every graded step is checked against its recorded digest and graded with the
  step's own verifier bundle (its recorded digest is checked too), sandbox and
  grader timeout, exactly as ``raidar.grading.regrade`` regrades the final
  snapshot (no coverage pass). The grade is written to the Assessment store
  (never the job), content-addressed by (bundle digest, step, snapshot digest):
  ``<store>/pre-fix-grades/<bundle>/<step>/<snapshot>.json``. A grade that is
  already stored is not graded again;
- ``regrade_pre_fix_path`` (``raidar assess --regrade --store``): grades them
  again and reports, per snapshot, whether the cases match the stored grade.

Nothing in the job directory is written. A snapshot whose digest no longer
matches, or a missing bundle, is reported as an error for that snapshot; the
scorer then reads no grade for it (``unavailable``, never a zero).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.contracts.snapshot import tree_digest
from raidar.grading.grader import grade_step
from raidar.grading.models import VerifierGrade
from raidar.grading.per_trial import per_trial
from raidar.grading.prefix_store import (
    GRADE_KIND,
    SCHEMA_VERSION,
    grade_path,
    read_pre_fix_grade,
)
from raidar.grading.records import RECORD_FILE, VERIFIER_RECORD_DIR, StepRecord, load_step_record
from raidar.grading.regrade import (
    RegradeError,
    RegradeOptions,
    docker_sandbox_factory,
    recorded_bundle,
    trial_dirs,
    write_store_json,
)

GATE_RECORD_PATH = "guardrail-gate/guardrail-gate.json"
PRE_FIX_KEY = "pre_fix_snapshots"


@dataclass(frozen=True)
class PreFixSnapshot:
    """One recorded pre-fix snapshot of a graded step."""

    trial_dir: Path
    step: str
    round: int
    path: Path
    digest: str
    record: StepRecord


def _gate_snapshots(agent_dir: Path) -> list[dict[str, Any]]:
    path = agent_dir / GATE_RECORD_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    entries = payload.get(PRE_FIX_KEY) if isinstance(payload, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def pre_fix_snapshots(trial_dir: Path) -> list[PreFixSnapshot]:
    """Every recorded pre-fix snapshot (with a digest) of the trial's graded steps."""
    steps_root = trial_dir / VERIFIER_RECORD_DIR / "steps"
    found: list[PreFixSnapshot] = []
    if not steps_root.is_dir():
        return found
    for record_dir in sorted(p for p in steps_root.iterdir() if (p / RECORD_FILE).is_file()):
        record = load_step_record(record_dir)
        agent_dir = trial_dir / "steps" / record.step / "agent"
        for entry in _gate_snapshots(agent_dir):
            digest, relative = entry.get("tree_digest"), entry.get("path")
            if isinstance(digest, str) and isinstance(relative, str):
                found.append(
                    PreFixSnapshot(
                        trial_dir,
                        record.step,
                        int(entry.get("round") or 0),
                        agent_dir / relative,
                        digest,
                        record,
                    )
                )
    return found


async def _grade(snapshot: PreFixSnapshot, options: RegradeOptions) -> tuple[VerifierGrade, str]:
    if not snapshot.path.is_dir():
        raise RegradeError(f"pre-fix snapshot missing: {snapshot.path}")
    if tree_digest(snapshot.path) != snapshot.digest:
        raise RegradeError(f"pre-fix snapshot changed since it was recorded: {snapshot.path}")
    bundle = recorded_bundle(snapshot.record, options)
    factory = options.sandbox_factory or docker_sandbox_factory(options.docker_context)
    sandbox = await factory(bundle)
    with tempfile.TemporaryDirectory(prefix="raidar-pre-fix-", dir=options.work_root) as work:
        result = await grade_step(
            bundle,
            snapshot.record.step,
            snapshot.path,
            sandbox,
            Path(work),
            coverage=False,
            timeout_s=snapshot.record.grader.timeout_s,
        )
    return result.grade, await sandbox.environment_id()


def _payload(snapshot: PreFixSnapshot, grade: VerifierGrade, environment: str) -> dict[str, Any]:
    return {
        "kind": GRADE_KIND,
        "schema_version": SCHEMA_VERSION,
        "step": snapshot.step,
        "round": snapshot.round,
        "snapshot_digest": snapshot.digest,
        "bundle_digest": snapshot.record.bundle.digest,
        "grader_environment_id": environment,
        "timeout_s": snapshot.record.grader.timeout_s,
        "graded_at": datetime.now(UTC).isoformat(),
        "summary": grade.summary(),
        "grade": grade.model_dump(mode="json"),
    }


def _entry(snapshot: PreFixSnapshot, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "trial_dir": str(snapshot.trial_dir),
        "step": snapshot.step,
        "round": snapshot.round,
        "snapshot_digest": snapshot.digest,
        "status": status,
        **extra,
    }


async def grade_pre_fix_snapshot(
    snapshot: PreFixSnapshot, store_root: Path, options: RegradeOptions
) -> dict[str, Any]:
    """Grade one pre-fix snapshot into the store (``stored`` when it already is)."""
    target = grade_path(store_root, snapshot.record.bundle.digest, snapshot.step, snapshot.digest)
    if target.is_file():
        return _entry(snapshot, "stored", path=str(target))
    try:
        grade, environment = await _grade(snapshot, options)
    except RegradeError as exc:
        return _entry(snapshot, "error", error=str(exc))
    payload = _payload(snapshot, grade, environment)
    write_store_json(target, payload)
    return _entry(snapshot, "graded", path=str(target), summary=payload["summary"])


async def grade_pre_fix_path(
    path: Path, store_root: Path, options: RegradeOptions, concurrency: int = 1
) -> dict[str, Any]:
    """Grade every pre-fix snapshot under a trial or job dir into the store.

    Up to ``concurrency`` trials are graded at once; each trial's snapshots in order.
    """

    async def trial_grades(trial: Path) -> list[dict[str, Any]]:
        return [
            await grade_pre_fix_snapshot(snapshot, store_root, options)
            for snapshot in pre_fix_snapshots(trial)
        ]

    entries = await per_trial(_trials(path), trial_grades, concurrency)
    return {
        "kind": "raidar-pre-fix-grading",
        "snapshots": entries,
        "errors": sum(1 for entry in entries if entry["status"] == "error"),
    }


def _trials(path: Path) -> list[Path]:
    try:
        return trial_dirs(path)
    except RegradeError:
        return []


async def regrade_pre_fix_path(
    path: Path, store_root: Path, options: RegradeOptions
) -> dict[str, Any]:
    """Grade every pre-fix snapshot again and compare its cases with the stored grade."""
    entries = []
    for trial in _trials(path):
        for snapshot in pre_fix_snapshots(trial):
            bundle_digest = snapshot.record.bundle.digest
            stored = read_pre_fix_grade(store_root, bundle_digest, snapshot.step, snapshot.digest)
            if stored is None:
                entries.append(_entry(snapshot, "not_graded"))
                continue
            try:
                grade, _ = await _grade(snapshot, options)
            except RegradeError as exc:
                entries.append(_entry(snapshot, "error", error=str(exc)))
                continue
            match = grade.cases == stored.cases and grade.infra_failure == stored.infra_failure
            entries.append(_entry(snapshot, "match" if match else "mismatch"))
    return {
        "kind": "raidar-pre-fix-regrade",
        "match": all(entry["status"] == "match" for entry in entries),
        "snapshots": entries,
    }
