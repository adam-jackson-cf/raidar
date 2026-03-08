"""Deterministic scenario-revision cloning helpers."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .schemas.scenario import ScenarioDefinition

REVISION_PATTERN = re.compile(r"^v(\d+)$")


@dataclass(frozen=True, slots=True)
class ScenarioCloneResult:
    """Artifacts created by scenario-revision cloning."""

    scenario_root: Path
    source_revision: str
    target_revision: str
    target_scenario_yaml: Path


def _validate_revision_label(revision: str) -> int:
    match = REVISION_PATTERN.fullmatch(revision)
    if match is None:
        raise ValueError(f"Invalid revision label '{revision}'. Expected format 'v###'.")
    return int(match.group(1))


def next_scenario_revision(source_revision: str) -> str:
    """Return the next deterministic revision label for a scenario."""

    numeric = _validate_revision_label(source_revision) + 1
    width = max(3, len(source_revision) - 1)
    return f"v{numeric:0{width}d}"


def clone_scenario_revision(
    *,
    scenario_root: Path,
    source_revision: str,
    target_revision: str | None = None,
) -> ScenarioCloneResult:
    """Clone one scenario revision to another and update scenario metadata."""

    source_dir = scenario_root / source_revision
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source scenario revision directory does not exist: {source_dir}")

    source_scenario_yaml = source_dir / "scenario.yaml"
    if not source_scenario_yaml.is_file():
        raise FileNotFoundError(f"Source scenario definition not found: {source_scenario_yaml}")

    resolved_target = target_revision or next_scenario_revision(source_revision)
    _validate_revision_label(resolved_target)
    if resolved_target == source_revision:
        raise ValueError("Target revision must differ from source revision.")

    target_dir = scenario_root / resolved_target
    if target_dir.exists():
        raise FileExistsError(f"Target scenario revision directory already exists: {target_dir}")

    shutil.copytree(source_dir, target_dir)

    try:
        target_scenario_yaml = target_dir / "scenario.yaml"
        scenario_def = ScenarioDefinition.from_yaml(target_scenario_yaml)
        scenario_def.scenario_revision = resolved_target
        scenario_def.to_yaml(target_scenario_yaml)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return ScenarioCloneResult(
        scenario_root=scenario_root,
        source_revision=source_revision,
        target_revision=resolved_target,
        target_scenario_yaml=target_scenario_yaml,
    )
