"""Scaffold template catalog and helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..audit.scaffold_manifest import generate_manifest, save_manifest


@dataclass(slots=True)
class ScaffoldSource:
    """Reference to a versioned scaffold template."""

    template: str
    version: str
    path: Path

    @property
    def manifest_path(self) -> Path:
        return self.path / "scaffold.manifest.json"


@dataclass(slots=True)
class PreparedWorkspace:
    """Metadata for a prepared workspace."""

    path: Path
    manifest_path: Path
    metadata_path: Path


def resolve_scaffold_source(root: Path, template: str, version: str) -> ScaffoldSource:
    """Resolve a template/version under the scaffold root."""

    source_path = root / template / version
    if not source_path.exists():
        raise FileNotFoundError(
            f"Scaffold template '{template}' version '{version}' not found under {root}."
        )

    manifest_path = source_path / "scaffold.manifest.json"
    if not manifest_path.exists():
        manifest = generate_manifest(
            source_path, template_name=template, template_version=version
        )
        save_manifest(manifest, manifest_path)

    return ScaffoldSource(template=template, version=version, path=source_path)


def record_scaffold_metadata(
    workspace: Path,
    source: ScaffoldSource,
    manifest_path: Path,
    rules_variant: str,
) -> Path:
    """Write scaffold metadata to the workspace to aid audits."""

    meta = {
        "template": source.template,
        "version": source.version,
        "rules_variant": rules_variant,
        "manifest": manifest_path.name,
    }
    meta_path = workspace / ".scaffold-meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta_path
