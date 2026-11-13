"""The definition an intake is building: its items, and the reply they make.

A ``Draft`` holds the ``block.Item`` objects of a ``define put`` in the order
they were first written. ``put`` replaces every item whose identity it names,
adds the rest and keeps what it does not name, so a call repeated, or a fix of
one item, leaves the definition as one call with the final text would.
``reply`` assembles exactly the object ``schemas.INTAKE_REPLY_SCHEMA`` describes,
so the checks on the reply (``intakecheck``), its rendering (``compact``), the
records and every reader of the file are the same as for any intake reply.
``sealed`` is the status ``define done`` was run with (``""`` before it, and
again after any later change).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from . import block
from .aspects import Finding
from .block import Item
from .core import JsonObject, rows, text
from .intakecheck import NEEDS_INPUT, READY
from .kinds import Kind

# What ``define done`` seals a draft with, and the status that makes of the reply.
SEALS = {"ready": READY, "ask": NEEDS_INPUT}


class DraftError(ValueError):
    """A saved draft that does not read."""


class Put(NamedTuple):
    """What a ``put`` did: the items it applied, and the findings for those it refused."""

    applied: list[Item]
    refused: list[Finding]


# ``drop`` reads ``retire: x`` and ``named: x`` as ``retire x`` and ``named x``
# (the lines ``show`` prints), and ``decision 03`` as ``decision 3``.
_KIND_COLON = re.compile(r"^(named|retire|decision)\s*:")
_DECISION_NUMBER = re.compile(r"^decision\s+0*([0-9]+)$")


@dataclass
class Draft:
    items: list[Item] = field(default_factory=list)
    sealed: str = ""

    def put(self, parsed: Sequence[Item]) -> Put:
        """Apply items, features first so a criterion finds its feature; return what was applied
        and refused."""
        self.sealed = ""
        done = Put([], [])
        ordered = sorted(parsed, key=lambda item: item.kind != block.FEATURE)
        for item in ordered:
            if item.kind == block.AC and not self._has(block.FEATURE, item.owner):
                done.refused.append(
                    Finding(
                        f"{item.key} line {item.line}: feature {item.owner} is not "
                        f"defined: put `feature {item.owner}: <name>` with it",
                        kind=Kind.GRAMMAR,
                    )
                )
                continue
            self._set(item)
            done.applied.append(item)
        return done

    def _has(self, kind: str, key: str) -> bool:
        return any(item.kind == kind and item.key == key for item in self.items)

    def _set(self, item: Item) -> None:
        for index, held in enumerate(self.items):
            if held.kind == item.kind and held.key == item.key:
                self.items[index] = item
                return
        self.items.append(item)

    def _taken(self, reference: str) -> list[Item]:
        """Return the items ``reference`` names, and the criteria of a feature it names."""
        wanted = _wanted(reference)
        named = [item for item in self.items if item.ref.casefold() == wanted]
        features = {item.key for item in named if item.kind == block.FEATURE}
        return [
            item
            for item in self.items
            if item in named or (item.kind == block.AC and item.owner in features)
        ]

    def drop(self, reference: str) -> int:
        """Remove the items ``reference`` names (a feature takes its criteria); return how many."""
        gone = self._taken(reference)
        self.items = [item for item in self.items if item not in gone]
        if gone:
            self.sealed = ""
        return len(gone)

    def refs(self) -> list[str]:
        """Return what ``drop`` can name, in the order ``ordered`` lists the items."""
        return [item.ref for item in self.ordered()]

    def kind(self, kind: str) -> list[Item]:
        return [item for item in self.items if item.kind == kind]

    def presumed(self) -> str:
        """Return the status the draft is checked as before it is sealed: asking when it holds a
        question."""
        return NEEDS_INPUT if self.kind(block.QUESTION) else READY

    def _grouped(self, feature: Item) -> list[Item]:
        """Return one ``feature`` followed by the criteria it owns, in draft order."""
        return [feature, *(a for a in self.kind(block.AC) if a.owner == feature.key)]

    def ordered(self) -> list[Item]:
        """Return the items grouped as a reader wants them: scalars, named features, each feature
        with its criteria, the rest."""
        grouped = [item for feature in self.kind(block.FEATURE) for item in self._grouped(feature)]
        decisions = sorted(self.kind(block.DECISION), key=lambda item: int(item.key))
        return [
            *self.kind(block.TITLE),
            *self.kind(block.OBJECTIVE),
            *self.kind(block.NAMED),
            *grouped,
            *decisions,
            *self.kind(block.RETIRE),
            *self.kind(block.QUESTION),
            *self.kind(block.REPLY),
        ]

    def _scalar(self, kind: str) -> str:
        found = self.kind(kind)
        return text(found[0].data, "text") if found else ""

    def reply(self, status: str) -> JsonObject:
        """Return the reply ``schemas.INTAKE_REPLY_SCHEMA`` describes, for ``status``.

        A ``needs_input`` reply sends the definition empty, as the schema says.
        """
        features: list[JsonObject] = [
            {
                **feature.data,
                "criteria": [ac.data for ac in self.kind(block.AC) if ac.owner == feature.key],
            }
            for feature in self.kind(block.FEATURE)
        ]
        decisions = sorted(self.kind(block.DECISION), key=lambda item: int(item.key))
        definition: JsonObject = {
            "title": self._scalar(block.TITLE),
            "objective": self._scalar(block.OBJECTIVE),
            "features": features,
            "decisions": [item.data for item in decisions],
            "retires": [item.data for item in self.kind(block.RETIRE)],
        }
        if status == NEEDS_INPUT:
            definition = {
                "title": "",
                "objective": "",
                "features": [],
                "decisions": [],
                "retires": [],
            }
        return {
            "features": [item.data for item in self.kind(block.NAMED)],
            "status": status,
            "questions": [item.data for item in self.kind(block.QUESTION)],
            "replies": [item.data for item in self.kind(block.REPLY)],
            "definition": definition,
        }

    def record(self) -> JsonObject:
        """Return the draft as the state file keeps it."""
        return {
            "sealed": self.sealed,
            "items": [
                {
                    "kind": item.kind,
                    "key": item.key,
                    "owner": item.owner,
                    "data": item.data,
                }
                for item in self.items
            ],
        }

    @classmethod
    def read(cls, record: object) -> Draft:
        """Return the draft a state file holds; raise ``DraftError`` when it does not read."""
        if not isinstance(record, dict) or not isinstance(record.get("items"), list):
            raise DraftError("the state is not a draft")
        sealed = record.get("sealed")
        if sealed not in ("", *SEALS):
            raise DraftError(f"the state is sealed as {sealed!r}")
        entries = rows(record, "items")
        if len(entries) != len(record["items"]):
            raise DraftError("the state holds an item that is not an object")
        return cls(_state_items(entries), str(sealed))


def _wanted(reference: str) -> str:
    """Return ``reference`` as ``Item.ref`` spells it (case-folded, spacing collapsed)."""
    folded = " ".join(reference.split()).casefold()
    named = _KIND_COLON.sub(lambda found: f"{found[1]} ", folded)
    return _DECISION_NUMBER.sub(r"decision \1", " ".join(named.split()))


def _item(entry: JsonObject) -> Item:
    kind, key, data = entry.get("kind"), entry.get("key"), entry.get("data")
    if kind not in block.BUILDERS or not isinstance(key, str):
        raise DraftError("the state holds an item that is not one")
    if not isinstance(data, dict) or (kind == block.DECISION and not block.NUMBER.fullmatch(key)):
        raise DraftError(f"the state's {kind} {key} does not read")
    return Item(str(kind), key, data, text(entry, "owner"))


def _state_items(entries: Sequence[JsonObject]) -> list[Item]:
    """Return the items a state file holds; raise ``DraftError`` for a criterion with no feature."""
    items = [_item(entry) for entry in entries]
    features = {item.key for item in items if item.kind == block.FEATURE}
    if any(i.kind == block.AC and i.owner not in features for i in items):
        raise DraftError("the state holds a criterion whose feature is missing")
    return items
