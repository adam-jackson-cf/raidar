"""Scaffold template catalog and helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..audit.workspace_diff import directory_fingerprint


@dataclass(slots=True)
class ScaffoldSource:
    """Reference to a task-version scaffold."""

    task_name: str
    task_version: str
    path: Path
    fingerprint: str


def resolve_scaffold_source(
    task_dir: Path,
    scaffold_root: str,
    *,
    task_name: str,
    task_version: str,
) -> ScaffoldSource:
    """Resolve a task-local scaffold root."""

    source_path = (task_dir / scaffold_root).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Scaffold root not found: {source_path}")

    return ScaffoldSource(
        task_name=task_name,
        task_version=task_version,
        path=source_path,
        fingerprint=directory_fingerprint(source_path),
    )


def record_scaffold_metadata(
    workspace: Path,
    source: ScaffoldSource,
) -> Path:
    """Write scaffold metadata to the workspace to aid audits."""

    meta = {
        "task": source.task_name,
        "task_version": source.task_version,
        "fingerprint": source.fingerprint,
    }
    meta_path = workspace / ".scaffold-meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta_path
