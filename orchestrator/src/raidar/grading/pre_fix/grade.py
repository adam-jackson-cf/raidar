"""Offline grades of the workspaces fix turns started from (``gate/1`` retained pre-fix trees).

With ``max_fix_rounds`` above zero, ``gate/1`` copies ``/app`` host-side before every fix turn
(the verifier's own snapshot excludes) under the step's agent logs
(``gate/pre_fix_<round>/app``, the entry's ``path`` relative to the agent logs) and records
each tree's ``tree_digest`` in the ``gate/1`` record of ``workflow/workflow.json``
(``pre_fix_snapshots``). This module grades those trees offline, beside the step's final snapshot:

- ``grade_pre_fix_path`` (run by ``raidar assess --rescore`` when a locked scorer reads pre-fix
  grades, ``verification-effort@1``): every pre-fix tree of every graded step is checked against
  its recorded digest and graded with the step's own verifier bundle (its recorded digest is
  checked too), sandbox and grader timeout, exactly as ``raidar.grading.regrade`` regrades the
  final snapshot (no coverage pass). The grade is written to the Assessment store (never the job),
  content-addressed by (bundle digest, step, snapshot digest):
  ``<store>/pre-fix-grades/<bundle>/<step>/<identity>/<snapshot>.json``. The identity binds the
  recorded image ID, timeout and host grading implementation. A grade already stored is not graded
  again;
- ``regrade_pre_fix_path`` (``raidar assess --regrade --store``): grades them again and reports,
  per snapshot, whether the cases match the stored grade.

Nothing in the job directory is written. A tree whose digest no longer matches, whose path leaves
the agent logs, or a missing bundle, is reported as an error for that snapshot; the scorer then
reads no grade for it (``unavailable``, never a zero). A step with no ``gate/1`` record, or none
retained, has no pre-fix snapshot. A step excluded from the solvable set is graded like any other:
the grade is the plain case pass rate, and which steps count is the scorers' business.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raidar.contract.snapshot import tree_digest
from raidar.grading.grader import grade_step
from raidar.grading.models import VerifierGrade
from raidar.grading.per_trial import per_trial
from raidar.grading.pre_fix.store import (
    GRADE_KIND,
    SCHEMA_VERSION,
    grade_path,
    read_pre_fix_grade,
)
from raidar.grading.records import (
    RECORD_FILE,
    VERIFIER_RECORD_DIR,
    StepRecord,
    grading_identity,
    load_step_record,
)
from raidar.grading.regrade import (
    RegradeError,
    RegradeOptions,
    docker_sandbox_factory,
    recorded_bundle,
    trial_dirs,
    write_store_json,
)

WORKFLOW_RECORD = "workflow/workflow.json"
GATE_KEY = "gate/1"
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
    contained: bool = True
    """False when the recorded path leaves the step's agent logs: it is never read."""


def _gate_snapshots(agent_dir: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((agent_dir / WORKFLOW_RECORD).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    gate = payload.get(GATE_KEY) if isinstance(payload, dict) else None
    entries = gate.get(PRE_FIX_KEY) if isinstance(gate, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def _inside(agent_dir: Path, relative: str) -> Path | None:
    """``agent_dir / relative`` when it stays inside the agent logs, else None."""
    path = (agent_dir / relative).resolve()
    return path if path.is_relative_to(agent_dir.resolve()) else None


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
            if not isinstance(digest, str) or not isinstance(relative, str):
                continue
            found.append(
                PreFixSnapshot(
                    trial_dir,
                    record.step,
                    int(entry.get("round") or 0),
                    agent_dir / relative,
                    digest,
                    record,
                    _inside(agent_dir, relative) is not None,
                )
            )
    return found


def _require_contained(snapshot: PreFixSnapshot) -> None:
    if not snapshot.contained:
        raise RegradeError(f"pre-fix snapshot path leaves the agent logs: {snapshot.path}")


async def _grade(snapshot: PreFixSnapshot, options: RegradeOptions) -> tuple[VerifierGrade, str]:
    _require_contained(snapshot)
    if not snapshot.path.is_dir():
        raise RegradeError(f"pre-fix snapshot missing: {snapshot.path}")
    if tree_digest(snapshot.path) != snapshot.digest:
        raise RegradeError(f"pre-fix snapshot changed since it was recorded: {snapshot.path}")
    bundle = recorded_bundle(snapshot.record, options)
    factory = options.sandbox_factory or docker_sandbox_factory(options.docker_context)
    sandbox = await factory(bundle)
    environment = await sandbox.environment_id()
    if environment != snapshot.record.grader.environment_id:
        raise RegradeError("pre-fix grader environment differs from the trial")
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
    return result.grade, environment


def _payload(snapshot: PreFixSnapshot, grade: VerifierGrade, environment: str) -> dict[str, Any]:
    return {
        "kind": GRADE_KIND,
        "schema_version": SCHEMA_VERSION,
        "identity": grading_identity(snapshot.record.grader),
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
    identity = grading_identity(snapshot.record.grader)
    target = grade_path(
        store_root, snapshot.record.bundle.digest, snapshot.step, snapshot.digest, identity=identity
    )
    try:
        _require_contained(snapshot)
        if not snapshot.path.is_dir() or tree_digest(snapshot.path) != snapshot.digest:
            raise RegradeError(f"pre-fix snapshot changed since it was recorded: {snapshot.path}")
        recorded_bundle(snapshot.record, options)
        if (
            read_pre_fix_grade(
                store_root,
                snapshot.record.bundle.digest,
                snapshot.step,
                snapshot.digest,
                identity=identity,
            )
            is not None
        ):
            return _entry(snapshot, "stored", path=str(target))
        grade, environment = await _grade(snapshot, options)
    except RegradeError as exc:
        return _entry(snapshot, "error", error=str(exc))
    if grade.infra_failure is not None:
        return _entry(
            snapshot, "error", error=f"pre-fix infrastructure failure: {grade.infra_failure.code}"
        )
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
            stored = read_pre_fix_grade(
                store_root,
                bundle_digest,
                snapshot.step,
                snapshot.digest,
                identity=grading_identity(snapshot.record.grader),
            )
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
