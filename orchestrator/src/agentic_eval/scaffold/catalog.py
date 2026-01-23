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
    """Reference to a versioned scaffold template."""

    template: str
    version: str
    path: Path
    manifest: ScaffoldManifest

    @property
    def manifest_path(self) -> Path:
        return self.path / "scaffold.manifest.json"


def resolve_scaffold_source(root: Path, template: str, version: str) -> ScaffoldSource:
    """Resolve a template/version under the scaffold root."""

    source_path = root / template / version
    if not source_path.exists():
        raise FileNotFoundError(
            f"Scaffold template '{template}' version '{version}' not found under {root}."
        )

    manifest_path = source_path / "scaffold.manifest.json"
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
    else:
        manifest = generate_manifest(
            source_path, template_name=template, template_version=version
        )
        save_manifest(manifest, manifest_path)

    # Ensure template metadata stays consistent
    if manifest.template != template or manifest.template_version != version:
        manifest.template = template
        manifest.template_version = version
        save_manifest(manifest, manifest_path)

    return ScaffoldSource(template=template, version=version, path=source_path, manifest=manifest)


def record_scaffold_metadata(
    workspace: Path,
    source: ScaffoldSource,
    workspace_manifest: Path,
    baseline_manifest: Path,
    rules_variant: str,
) -> Path:
    """Write scaffold metadata to the workspace to aid audits."""

    meta = {
        "template": source.template,
        "version": source.version,
        "fingerprint": source.manifest.fingerprint,
        "rules_variant": rules_variant,
        "workspace_manifest": workspace_manifest.name,
        "baseline_manifest": baseline_manifest.name,
    }
    meta_path = workspace / ".scaffold-meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta_path
