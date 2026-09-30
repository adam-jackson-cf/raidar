"""Move a run executed under a version-1 lock onto the sectioned lock (``raidar migrate-run-lock``).

Version 1 locks hashed everything (scorer implementation digests included)
into ``lock_sha256``, so any scorer edit orphaned the runs executed under
them. The sectioned lock's ``lock_sha256`` is the digest of its ``execution``
section only. A run moves to the new lock only when that is provably the same
experiment:

1. the executed lock is a valid version-1 lock whose body hashes to its
   ``lock_sha256``, and that digest is the one ``run.json`` recorded;
2. the current lock is a valid sealed lock;
3. the execution section rebuilt from the executed lock's own records
   (design, problems, files, problem and component references, hashed
   conditions, arm digests, Harbor and harness versions, and every tool the
   current execution section pins) equals the current lock's execution
   section, field for field.

On success the executed lock is kept byte for byte in the run directory
(``executed-lock.json``) and ``run.json`` gets the new ``lock_sha256`` plus a
``lock_migration`` record naming both digests. ``plan.json`` and the
trajectory records are not touched: their ``hashes.lock`` stays the digest
the trials were executed under, which ``lock_migration`` maps to the new one.
Re-running the migration on a migrated run is a no-op.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.spec.canonical import digest_json
from raidar.spec.lock import (
    EXECUTION_REF_KINDS,
    LOCK_VERSION,
    execution_section,
    lock_integrity_issues,
)

EXECUTED_LOCK_FILE = "executed-lock.json"
RUN_MANIFEST = "run.json"
EXECUTED_LOCK_VERSION = 1


class LockMigrationError(ValueError):
    """The run cannot be moved onto the current lock."""


@dataclass(frozen=True)
class MigrationResult:
    run_dir: Path
    executed_lock_sha256: str
    lock_sha256: str
    migrated: bool
    """False when the run already carried this migration (no-op)."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise LockMigrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LockMigrationError(f"{path} is not a JSON object")
    return loaded


def _checked_executed(executed: Mapping[str, Any], path: Path) -> str:
    if executed.get("lock_version") != EXECUTED_LOCK_VERSION:
        raise LockMigrationError(
            f"{path} is lock_version {executed.get('lock_version')!r}; only version "
            f"{EXECUTED_LOCK_VERSION} locks are migrated"
        )
    body = {key: value for key, value in executed.items() if key != "lock_sha256"}
    if digest_json(body) != executed.get("lock_sha256"):
        raise LockMigrationError(f"{path}: lock_sha256 does not match the lock body")
    return str(executed["lock_sha256"])


def execution_from_executed(
    executed: Mapping[str, Any], current_tools: Mapping[str, str]
) -> dict[str, object]:
    """The execution section a version-1 lock's own records describe.

    Version 1 recorded tool versions without their reference kind, so each
    tool the current execution section pins is taken from that record; a tool
    it never recorded is absent and the comparison fails.
    """
    versions = executed["versions"]
    recorded_tools = versions.get("tools") or {}
    references = executed.get("references") or {}
    return execution_section(
        executed["spec"],
        problems=executed["problems"],
        files=executed["files"],
        references={kind.value: references.get(kind.value, {}) for kind in EXECUTION_REF_KINDS},
        conditions=executed["conditions"],
        arms=executed["arms"],
        versions={"harbor": versions["harbor"], "harnesses": versions["harnesses"]},
        tools={tool: recorded_tools[tool] for tool in current_tools if tool in recorded_tools},
    )


def _differences(expected: Mapping[str, Any], found: Mapping[str, Any]) -> list[str]:
    keys = sorted(set(expected) | set(found))
    return [f"execution.{key}" for key in keys if expected.get(key) != found.get(key)]


def migrate_run_lock(run_dir: Path, executed_lock: Path, current_lock: Path) -> MigrationResult:
    """Point ``run_dir`` at ``current_lock`` after proving the execution is identical."""
    manifest_path = run_dir / RUN_MANIFEST
    manifest = _read_object(manifest_path)
    executed = _read_object(executed_lock)
    executed_sha = _checked_executed(executed, executed_lock)
    current = _read_object(current_lock)
    issues = lock_integrity_issues(current)
    if issues:
        raise LockMigrationError(f"{current_lock} is not a valid lock: {'; '.join(issues)}")
    target = str(current["lock_sha256"])
    previous = manifest.get("lock_migration")
    already = isinstance(previous, Mapping) and previous.get("executed_lock_sha256") == executed_sha
    if already and manifest.get("lock_sha256") == target:
        return MigrationResult(run_dir, executed_sha, target, migrated=False)
    if manifest.get("lock_sha256") != executed_sha:
        raise LockMigrationError(
            f"{run_dir} was executed under lock {manifest.get('lock_sha256')}, "
            f"not {executed_sha} ({executed_lock})"
        )
    execution = current["execution"]
    rebuilt = execution_from_executed(executed, execution.get("tools") or {})
    differing = _differences(rebuilt, execution)
    if differing:
        raise LockMigrationError(
            f"{current_lock} does not describe the experiment {run_dir} executed: "
            f"{', '.join(differing)} differ"
        )
    (run_dir / EXECUTED_LOCK_FILE).write_bytes(executed_lock.read_bytes())
    manifest["lock_sha256"] = target
    manifest["lock_migration"] = {
        "executed_lock": EXECUTED_LOCK_FILE,
        "executed_lock_sha256": executed_sha,
        "executed_lock_version": EXECUTED_LOCK_VERSION,
        "lock_sha256": target,
        "lock_version": LOCK_VERSION,
        "verified": "execution section rebuilt from the executed lock equals the current lock's",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return MigrationResult(run_dir, executed_sha, target, migrated=True)
