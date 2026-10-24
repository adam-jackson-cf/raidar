"""Where pre-fix snapshot grades live in the Assessment store, and how they are read.

The contract shared by the offline grader (``raidar.grading.prefix_grade``)
and the scorer that reads the grades (``gate-effort@10``): one JSON file per
graded snapshot, content-addressed by (bundle digest, step, snapshot digest),
``<store>/pre-fix-grades/<bundle>/<step>/<snapshot>.json``. Kept apart from
the grader so the scorer's identity covers only this reading contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from raidar.grading.models import VerifierGrade

PRE_FIX_GRADES = "pre-fix-grades"
GRADE_KIND = "raidar-pre-fix-grade"
SCHEMA_VERSION = 1


def grade_path(store_root: Path, bundle_digest: str, step: str, snapshot_digest: str) -> Path:
    """Where the grade of one pre-fix snapshot lives in the Assessment store."""
    return store_root / PRE_FIX_GRADES / bundle_digest / step / f"{snapshot_digest}.json"


def read_pre_fix_grade(
    store_root: Path, bundle_digest: str, step: str, snapshot_digest: str
) -> VerifierGrade | None:
    """The stored grade of one pre-fix snapshot, or None when it was not graded."""
    path = grade_path(store_root, bundle_digest, step, snapshot_digest)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("kind") != GRADE_KIND:
        return None
    if payload.get("snapshot_digest") != snapshot_digest or payload.get("step") != step:
        return None
    return VerifierGrade.model_validate(payload["grade"])
