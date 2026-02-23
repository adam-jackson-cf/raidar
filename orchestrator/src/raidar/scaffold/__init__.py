"""Scaffold utilities."""

from .catalog import ScaffoldSource, record_scaffold_metadata, resolve_scaffold_source

__all__ = [
    "ScaffoldSource",
    "resolve_scaffold_source",
    "record_scaffold_metadata",
]
