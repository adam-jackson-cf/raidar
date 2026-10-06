"""Where offline coverage runs live in the Assessment store, and how they are read.

The contract shared by the offline producer (``raidar.verifier.coverage_offline``)
and the scorer that reads it (``code-quality@2``): one JSON file per graded step
snapshot whose trial ran with the coverage pass deferred
(``protocol.verifier_coverage: offline``), content-addressed by (bundle digest,
step, snapshot digest), ``<store>/offline-coverage/<bundle>/<step>/<snapshot>.json``.
It holds what the in-trial pass would have written beside the step record: the
parity verdict and its differences (``record.json`` ``coverage``) and the
fork-shaped G input (``g-kernel-input.json``), byte-for-byte the same payload.
Kept apart from the producer so the scorer's identity covers only this reading
contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar.verifier.models import ParityVerdict

OFFLINE_COVERAGE = "offline-coverage"
COVERAGE_KIND = "raidar-offline-coverage"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class OfflineCoverage:
    """One stored offline coverage run: the parity verdict and the G input it produced."""

    parity: ParityVerdict
    differences: tuple[str, ...]
    fork: dict[str, Any] | None


def coverage_path(store_root: Path, bundle_digest: str, step: str, snapshot_digest: str) -> Path:
    """Where the offline coverage run of one step snapshot lives in the Assessment store."""
    return store_root / OFFLINE_COVERAGE / bundle_digest / step / f"{snapshot_digest}.json"


def read_offline_coverage(
    store_root: Path, bundle_digest: str, step: str, snapshot_digest: str
) -> OfflineCoverage | None:
    """The stored offline coverage run of one step snapshot, or None when not produced."""
    path = coverage_path(store_root, bundle_digest, step, snapshot_digest)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != COVERAGE_KIND:
        return None
    if payload.get("snapshot_digest") != snapshot_digest or payload.get("step") != step:
        return None
    fork = payload.get("fork")
    return OfflineCoverage(
        parity=ParityVerdict.model_validate(payload["parity"]),
        differences=tuple(payload.get("differences") or ()),
        fork=fork if isinstance(fork, dict) else None,
    )
