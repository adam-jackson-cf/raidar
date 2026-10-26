"""What every module of the payload shares: the JSON object type, the readers of its fields
and the record keys.

factory-x-loops' ``core.contracts`` (``JsonObject``), ``core.crossref`` and the three keys of
``core.recordnames`` the define capability reads, in one module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, cast

type JsonObject = dict[str, object]

BEHAVIOUR_FROM_KEY: Final = "behaviour_from"
PROBES_OWED_KEY: Final = "probes_owed"
UNIT_RULES_KEY: Final = "unit_rules"


def rows(value: Mapping[str, object], key: str) -> list[JsonObject]:
    items = value.get(key)
    if not isinstance(items, list):
        return []
    return [cast(JsonObject, item) for item in items if isinstance(item, dict)]


def text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    return item if isinstance(item, str) else ""


def strings(value: Mapping[str, object], key: str) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str)]


def blank(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if isinstance(item, str):
        return not item.strip()
    return not item
