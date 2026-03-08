"""Starter catalog and metadata helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..audit.workspace_diff import directory_fingerprint


@dataclass(slots=True)
class StarterSource:
    """Reference to a scenario-revision starter."""

    scenario_name: str
    scenario_revision: str
    path: Path
    fingerprint: str


def resolve_starter_source(
    scenario_dir: Path,
    starter_root: str,
    *,
    scenario_name: str,
    scenario_revision: str,
) -> StarterSource:
    """Resolve a scenario-local starter root."""

    source_path = (scenario_dir / starter_root).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Starter root not found: {source_path}")

    return StarterSource(
        scenario_name=scenario_name,
        scenario_revision=scenario_revision,
        path=source_path,
        fingerprint=directory_fingerprint(source_path),
    )


def record_starter_metadata(workspace: Path, source: StarterSource) -> Path:
    """Write starter metadata to the workspace to aid audits."""

    meta = {
        "scenario": source.scenario_name,
        "scenario_revision": source.scenario_revision,
        "fingerprint": source.fingerprint,
    }
    meta_path = workspace / ".starter-meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta_path
