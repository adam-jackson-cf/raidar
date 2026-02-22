"""Scaffold template catalog and helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..audit.scaffold_manifest import (
    ScaffoldManifest,
    generate_manifest,
    load_manifest,
    save_manifest,
)


@dataclass(slots=True)
class ScaffoldSource:
    """Reference to a task-version scaffold."""

    task_name: str
    task_version: str
    path: Path
    manifest: ScaffoldManifest

    @property
    def manifest_path(self) -> Path:
        return self.path / "scaffold.manifest.json"


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

    manifest_path = source_path / "scaffold.manifest.json"
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
    else:
        manifest = generate_manifest(
            source_path,
            template_name=task_name,
            template_version=task_version,
        )
        save_manifest(manifest, manifest_path)

    if manifest.template != task_name or manifest.template_version != task_version:
        manifest.template = task_name
        manifest.template_version = task_version
        save_manifest(manifest, manifest_path)

    return ScaffoldSource(
        task_name=task_name,
        task_version=task_version,
        path=source_path,
        manifest=manifest,
    )


def record_scaffold_metadata(
    workspace: Path,
    source: ScaffoldSource,
    workspace_manifest: Path,
    baseline_manifest: Path,
) -> Path:
    """Write scaffold metadata to the workspace to aid audits."""

    meta = {
        "task": source.task_name,
        "task_version": source.task_version,
        "fingerprint": source.manifest.fingerprint,
        "workspace_manifest": workspace_manifest.name,
        "baseline_manifest": baseline_manifest.name,
    }
    meta_path = workspace / ".scaffold-meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta_path
