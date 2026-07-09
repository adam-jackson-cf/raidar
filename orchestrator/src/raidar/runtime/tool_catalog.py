"""Catalog-backed dependency probes for task images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class ToolCatalogError(ValueError):
    """Raised when a dependency has no catalog entry."""


@dataclass(frozen=True, slots=True)
class ToolCatalogEntry:
    """One dependency probe command."""

    category: str
    name: str
    probe: tuple[str, ...]
    installed_value: str | None = None


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_CATALOG_PATH = REPO_ROOT / "environments" / "tools.yaml"


def _load_tool_catalog(path: Path = TOOL_CATALOG_PATH) -> tuple[ToolCatalogEntry, ...]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ToolCatalogError(f"Tool catalog not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ToolCatalogError(f"Invalid tool catalog YAML: {path}") from exc
    if not isinstance(payload, dict):
        raise ToolCatalogError(f"Tool catalog must be a mapping: {path}")
    tools = payload.get("tools")
    if not isinstance(tools, dict):
        raise ToolCatalogError(f"Tool catalog missing tools mapping: {path}")

    entries: list[ToolCatalogEntry] = []
    for category, category_payload in tools.items():
        if not isinstance(category, str) or not isinstance(category_payload, dict):
            raise ToolCatalogError(f"Tool catalog category must be a mapping: {category!r}")
        for name, item in category_payload.items():
            entries.append(_catalog_entry(category, name, item))
    return tuple(entries)


def _catalog_entry(category: str, name: object, item: object) -> ToolCatalogEntry:
    if not isinstance(name, str) or not isinstance(item, dict):
        raise ToolCatalogError(f"Tool catalog entry must be a mapping: {category}.{name}")
    probe = item.get("probe")
    if not isinstance(probe, list) or not probe or not all(isinstance(arg, str) for arg in probe):
        raise ToolCatalogError(f"Tool catalog entry {category}.{name} must define probe argv")
    installed_value = item.get("installed_value")
    if installed_value is not None and not isinstance(installed_value, str):
        raise ToolCatalogError(f"Tool catalog entry {category}.{name} installed_value must be text")
    return ToolCatalogEntry(
        category=category,
        name=name,
        probe=tuple(probe),
        installed_value=installed_value,
    )


def _catalog() -> dict[tuple[str, str], ToolCatalogEntry]:
    return {(entry.category, entry.name): entry for entry in _load_tool_catalog()}


def tool_catalog_payload() -> dict[str, dict[str, list[str] | str | None]]:
    """Return stable catalog material for cache keys."""

    return {
        f"{entry.category}.{entry.name}": {
            "probe": list(entry.probe),
            "installed_value": entry.installed_value,
        }
        for entry in sorted(_load_tool_catalog(), key=lambda item: (item.category, item.name))
    }


def probe_command(category: str, name: str) -> list[str]:
    """Return a cataloged probe command or fail closed."""

    try:
        return list(_catalog()[(category, name)].probe)
    except KeyError as exc:
        raise ToolCatalogError(f"No tool catalog entry for {category}.{name}") from exc


def installed_probe_value(category: str, name: str) -> str | None:
    """Return a fixed probe value for presence-only dependencies."""

    try:
        return _catalog()[(category, name)].installed_value
    except KeyError as exc:
        raise ToolCatalogError(f"No tool catalog entry for {category}.{name}") from exc
