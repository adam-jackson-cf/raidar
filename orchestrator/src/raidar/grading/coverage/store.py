"""Where offline coverage runs live in the Assessment store, and how they are read.

The producer stores a parity decision for one trial's retained grading evidence.
The path binds the bundle, step and snapshot to the grader image ID, timeout,
host grading implementation, grade and grading-evidence digest. Legacy unbound entries are
not consumed by production readers; rescore produces a bound entry. Reusable
coverage measurements are stored separately and parity is recomputed against
each trial's own grading run.
Kept apart from the producer so the scorer's identity covers only this reading
contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.grading.models import ParityVerdict

OFFLINE_COVERAGE = "offline-coverage"
COVERAGE_KIND = "raidar-offline-coverage"
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class OfflineCoverage:
    """One stored offline coverage run: the parity verdict and the G input it produced."""

    parity: ParityVerdict
    differences: tuple[str, ...]
    fork: dict[str, Any] | None


def coverage_path(
    store_root: Path, bundle_digest: str, step: str, snapshot_digest: str, *, identity: str = ""
) -> Path:
    """Where the offline coverage run of one step snapshot lives in the Assessment store."""
    root = store_root / OFFLINE_COVERAGE / bundle_digest / step
    return (
        root / identity / f"{snapshot_digest}.json"
        if identity
        else root / f"{snapshot_digest}.json"
    )


def _matches_coverage_identity(
    payload: Any, step: str, snapshot_digest: str, identity: str
) -> bool:
    expected = {
        "kind": COVERAGE_KIND,
        "schema_version": SCHEMA_VERSION,
        "step": step,
        "snapshot_digest": snapshot_digest,
        "identity": identity,
    }
    return (
        bool(identity)
        and isinstance(payload, dict)
        and all(payload.get(key) == value for key, value in expected.items())
    )


def read_offline_coverage(
    store_root: Path, bundle_digest: str, step: str, snapshot_digest: str, *, identity: str
) -> OfflineCoverage | None:
    """The stored offline coverage run of one step snapshot, or None when not produced."""
    path = coverage_path(store_root, bundle_digest, step, snapshot_digest, identity=identity)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not _matches_coverage_identity(payload, step, snapshot_digest, identity):
        return None
    fork = payload.get("fork")
    return OfflineCoverage(
        parity=ParityVerdict.model_validate(payload["parity"]),
        differences=tuple(payload.get("differences") or ()),
        fork=fork if isinstance(fork, dict) else None,
    )
