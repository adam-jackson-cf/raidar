"""Scorer definition path helpers."""

from __future__ import annotations

from pathlib import Path

from raidar.scenario_paths import resolve_relative_file, validate_relative_path


def scorer_definitions_dir() -> Path:
    """Return the package-local scorer definition root."""

    return Path(__file__).parent / "definitions"


def validate_scorer_relative_path(value: str, *, field_name: str) -> str:
    """Validate lexical constraints for scorer-owned paths."""

    return validate_relative_path(value, field_name=field_name, root_name="scorer definitions")


def resolve_scorer_definition_file(
    relative_path: str,
    *,
    field_name: str,
    must_exist: bool = True,
) -> Path:
    """Resolve a scorer-authored file path inside scorer definitions."""

    return resolve_relative_file(
        scorer_definitions_dir(),
        relative_path,
        field_name=field_name,
        root_name="scorer definitions",
        must_exist=must_exist,
    )
