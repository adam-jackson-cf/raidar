"""Scenario-local path resolution helpers."""

from __future__ import annotations

from pathlib import Path


class RelativePathError(ValueError):
    """Raised when an authored relative path escapes its owning root."""


class ScenarioPathError(RelativePathError):
    """Raised when a scenario-authored path escapes the scenario revision."""


def validate_relative_path(value: str, *, field_name: str, root_name: str) -> str:
    """Validate lexical constraints for authored relative paths."""

    if not value or not value.strip():
        raise RelativePathError(f"{field_name} must not be empty")
    path = Path(value)
    if path.is_absolute():
        raise RelativePathError(f"{field_name} must be relative to the {root_name}")
    if any(part == ".." for part in path.parts):
        raise RelativePathError(f"{field_name} must not contain parent traversal")
    return value


def validate_scenario_relative_path(value: str, *, field_name: str) -> str:
    """Validate lexical constraints for scenario-relative paths."""

    return validate_relative_path(value, field_name=field_name, root_name="scenario revision")


def resolve_relative_file(
    root_dir: Path,
    relative_path: str,
    *,
    field_name: str,
    root_name: str,
    must_exist: bool = True,
) -> Path:
    """Resolve an authored file path inside its owning root."""

    validate_relative_path(relative_path, field_name=field_name, root_name=root_name)
    root = root_dir.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(root):
        raise RelativePathError(f"{field_name} must stay inside the {root_name}")
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"{field_name} file not found: {resolved}")
    return resolved


def resolve_scenario_relative_file(
    scenario_dir: Path,
    relative_path: str,
    *,
    field_name: str,
    must_exist: bool = True,
) -> Path:
    """Resolve a scenario-authored file path inside a scenario revision."""

    return resolve_relative_file(
        scenario_dir,
        relative_path,
        field_name=field_name,
        root_name="scenario revision",
        must_exist=must_exist,
    )
