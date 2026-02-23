"""Deterministic task-version cloning helpers."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .schemas.task import TaskDefinition

VERSION_PATTERN = re.compile(r"^v(\d+)$")


@dataclass(frozen=True, slots=True)
class TaskCloneResult:
    """Artifacts created by task-version cloning."""

    task_root: Path
    source_version: str
    target_version: str
    target_task_yaml: Path


def _validate_version_label(version: str) -> int:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"Invalid version label '{version}'. Expected format 'v###'.")
    return int(match.group(1))


def next_task_version(source_version: str) -> str:
    """Return the next deterministic version label for a task."""
    numeric = _validate_version_label(source_version) + 1
    width = max(3, len(source_version) - 1)
    return f"v{numeric:0{width}d}"


def clone_task_version(
    *,
    task_root: Path,
    source_version: str,
    target_version: str | None = None,
) -> TaskCloneResult:
    """Clone one task version to another and update task version metadata."""
    source_dir = task_root / source_version
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source task version directory does not exist: {source_dir}")

    source_task_yaml = source_dir / "task.yaml"
    if not source_task_yaml.is_file():
        raise FileNotFoundError(f"Source task definition not found: {source_task_yaml}")

    resolved_target = target_version or next_task_version(source_version)
    _validate_version_label(resolved_target)
    if resolved_target == source_version:
        raise ValueError("Target version must differ from source version.")

    target_dir = task_root / resolved_target
    if target_dir.exists():
        raise FileExistsError(f"Target task version directory already exists: {target_dir}")

    shutil.copytree(source_dir, target_dir)

    try:
        target_task_yaml = target_dir / "task.yaml"
        task_def = TaskDefinition.from_yaml(target_task_yaml)
        task_def.version = resolved_target
        task_def.to_yaml(target_task_yaml)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return TaskCloneResult(
        task_root=task_root,
        source_version=source_version,
        target_version=resolved_target,
        target_task_yaml=target_task_yaml,
    )
