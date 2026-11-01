"""What a definition owes the acceptance probes: an example for every arrow outcome, list item and
example section.

The engine derives a probe from an issue's worked example blocks by itself
(``capability.accept.golden``). It cannot derive the cases an issue states in
one sentence, so the intake writes them as ``example: <input> -> <expected>``
lines and the engine runs each as written. ``define done`` refuses until the
definition owes nothing:

- an **arrow outcome** (``U12``: ``X`` → ``Y``, unit rules 2) needs an example
  under a criterion citing ``U12`` whose input carries ``X``'s backticked literal
  (when ``X`` has one) and whose expected side carries ``Y``'s;
- a **labelled list** (``U7``: ``Flags: -v, -q``) needs, for every item, an
  example under a criterion citing ``U7`` that has the item as a token of its
  input (token rule 2 of ``enumerated``, which the run-health check C7 and the
  gate apply to the tests): an item that is a prefix operator, one the input
  uses only after another operator or an opening group, must also begin the
  input (or a quoted value of it), not only follow an operator;
- an **example section** the engine cannot turn into a probe (a placeholder in
  its expected block, a command the entry does not start, ...) needs an example
  under a criterion citing one of its lines, so it is never dropped silently.

An example's input counts only when it is one backticked literal: that is what
a probe runs. An arrow with no backticked outcome, or with several arrows,
states no input-to-outcome pair a probe could run; it is advisory
(``probe.underivable``): the intake is told, ``done`` is not refused.

The engine works out the issue side once per intake (``Owed``, built by
``capability.accept.obligations``) and hands it to this check, here and in the
``define`` tool's context, so the tool and the engine's own check read the same
list. This module imports nothing of ``accept``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import aspects, issuetext, labelled
from .aspects import Finding
from .compact import Units, cited
from .core import JsonObject, rows, strings, text
from .enumerated import Exercised, item_exercised
from .kinds import Kind

OUTCOME = "outcome"
ENUMERATION = "enumeration"
_ARROW = re.compile(r"→|->")
_WORD = re.compile(r"[A-Za-z]+")
# The words that may join the two spans of an example's input: the command line, then the standard
# input.
_JOINERS = frozenset({"with", "and", "plus", "on", "from", "given", "reading", "fed"})
MAX_PARTS = 2
SHOWN_ITEMS = 8


@dataclass(frozen=True)
class Arrow:
    """An arrow outcome unit: the literal before the arrow (if any) and the one after it."""

    id: str
    left: str | None
    right: str


@dataclass(frozen=True)
class ListUnit:
    """A labelled list unit: its label and its distinct items."""

    id: str
    label: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class Section:
    """An example section no probe can be derived from: its id, the lines that quote it and why."""

    id: str
    units: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Loose:
    """A unit that states an arrow no probe could run, and why (advisory)."""

    id: str
    reason: str


@dataclass(frozen=True)
class Owed:
    """The issue's side of what a definition owes the probes."""

    arrows: tuple[Arrow, ...] = ()
    lists: tuple[ListUnit, ...] = ()
    sections: tuple[Section, ...] = ()
    loose: tuple[Loose, ...] = ()

    def record(self) -> JsonObject:
        return {
            "arrows": [{"id": a.id, "left": a.left, "right": a.right} for a in self.arrows],
            "lists": [{"id": u.id, "label": u.label, "items": list(u.items)} for u in self.lists],
            "sections": [
                {"id": s.id, "units": list(s.units), "reason": s.reason} for s in self.sections
            ],
            "loose": [{"id": item.id, "reason": item.reason} for item in self.loose],
        }

    @classmethod
    def read(cls, record: JsonObject) -> Owed:
        return cls(
            tuple(
                Arrow(
                    text(a, "id"),
                    text(a, "left") if a.get("left") is not None else None,
                    text(a, "right"),
                )
                for a in rows(record, "arrows")
            ),
            tuple(
                ListUnit(text(u, "id"), text(u, "label"), tuple(strings(u, "items")))
                for u in rows(record, "lists")
            ),
            tuple(
                Section(text(s, "id"), tuple(strings(s, "units")), text(s, "reason"))
                for s in rows(record, "sections")
            ),
            tuple(Loose(text(i, "id"), text(i, "reason")) for i in rows(record, "loose")),
        )


@dataclass(frozen=True)
class Case:
    """One ``example:`` line of a criterion: its sources and its two sides.

    ``parts`` is the program's input when the input side is one backticked
    literal (the command line, or the standard input), or two joined by a word
    such as ``with`` (the command line, then the standard input), which is what a
    probe runs; empty when it is prose or more spans, which no probe can run
    (``probe.underivable``).
    """

    criterion: str
    units: tuple[str, ...]
    given: str
    expected: str
    index: int = 1

    @property
    def parts(self) -> tuple[str, ...]:
        """The program's input: one code span, or two joined by ``with``/``and``/``on``, else
        none."""
        spans = issuetext.code_spans(self.given)
        blanked = issuetext.blank_code(self.given).replace("\x00", " ")
        words = [word.lower() for word in _WORD.findall(blanked)]
        if (
            not 1 <= len(spans) <= MAX_PARTS
            or len(words) > len(spans) - 1
            or any(word not in _JOINERS for word in words)
        ):
            return ()
        return tuple(spans)

    @property
    def text(self) -> str:
        """The input as the token rule reads it: the parts, one per line."""
        return "\n".join(self.parts)


def cases(reply: JsonObject) -> tuple[Case, ...]:
    """Return every example of a reply's behaviour criteria, in order."""
    found: list[Case] = []
    definition = reply.get("definition")
    body = definition if isinstance(definition, dict) else {}
    for feature in rows(body, "features"):
        for criterion in rows(feature, "criteria"):
            if text(criterion, "kind") == aspects.REGRESSION:
                continue
            units = cited(" ".join(strings(criterion, "sources")))
            found += [
                Case(
                    text(criterion, "id"),
                    units,
                    text(example, "input"),
                    text(example, "expected"),
                    number,
                )
                for number, example in enumerate(rows(criterion, "examples"), start=1)
            ]
    return tuple(found)


def _literals(value: str) -> list[str]:
    return [aspects.literal_value(span) for span in issuetext.code_spans(value)]


def arrow_of(unit_id: str, said: str) -> Arrow | str:
    """Return the arrow outcome ``said`` states, or why it states no input-to-outcome pair."""
    found = list(_ARROW.finditer(issuetext.blank_code(said)))
    if len(found) != 1:
        return f"`{unit_id}` has {len(found)} arrows, not one input-to-outcome pair"
    left, right = _literals(said[: found[0].start()]), _literals(said[found[0].end() :])
    if not right:
        return f"`{unit_id}` names no backticked outcome after its arrow"
    return Arrow(unit_id, left[-1] if left else None, right[0])


def arrows(units: Units) -> tuple[tuple[Arrow, ...], tuple[Loose, ...]]:
    """Return the arrow outcomes of ``units`` and the arrow units that state no probe-able pair."""
    good: list[Arrow] = []
    loose: list[Loose] = []
    for unit in units.items:
        if OUTCOME not in unit.signals:
            continue
        found = arrow_of(unit.id, unit.text)
        if isinstance(found, str):
            loose.append(Loose(unit.id, found))
        else:
            good.append(found)
    return tuple(good), tuple(loose)


def lists(units: Units) -> tuple[ListUnit, ...]:
    """Return the labelled list units of ``units``, with their items."""
    found = []
    for unit in units.items:
        listing = labelled.labelled_list(unit.text) if ENUMERATION in unit.signals else None
        if listing is not None:
            found.append(ListUnit(unit.id, listing.label, listing.items))
    return tuple(found)


def carries_arrow(case: Case, arrow: Arrow) -> bool:
    """Whether ``case`` cites the arrow's unit, has its left literal in the input and its right in
    the expected side."""
    return (
        arrow.id in case.units
        and bool(case.parts)
        and (arrow.left is None or aspects.appears(arrow.left, case.text))
        and aspects.appears(arrow.right, case.expected)
    )


def carries_item(case: Case, unit: ListUnit, item: str) -> bool:
    """Whether ``case`` cites the list's unit and has ``item`` as a token of its input."""
    return (
        unit.id in case.units
        and bool(case.parts)
        and item_exercised(item, Exercised(case.text, frozenset()), unit.items)
    )


def untested_items(unit: ListUnit, shown: Sequence[Case]) -> tuple[str, ...]:
    """Return the items of a list no example of ``shown`` has in its input."""
    return tuple(
        item for item in unit.items if not any(carries_item(case, unit, item) for case in shown)
    )


def _arrow_finding(arrow: Arrow) -> Finding:
    left = f"`{arrow.left}` in its input and " if arrow.left else ""
    return Finding(
        f"`{arrow.id}` states an outcome with an arrow: write an `example:` under a "
        f"criterion that cites {arrow.id}, with {left}`{arrow.right}` in its "
        "expected side (the input is the program's input in backticks: its command "
        "line, or its standard input, or both joined by `with`)",
        kind=Kind.PROBE_ARROW,
    )


def _list_finding(unit: ListUnit, missing: Sequence[str]) -> Finding:
    shown = ", ".join(f"`{item}`" for item in missing[:SHOWN_ITEMS])
    more = len(missing) - SHOWN_ITEMS
    tail = f" and {more} more" if more > 0 else ""
    return Finding(
        f"`{unit.id}` lists {len(unit.items)} items ({unit.label}) and no `example:` "
        f"under a criterion citing {unit.id} has {shown}{tail} in its input: write "
        "one example per item (the input is the program's input in backticks: its "
        "command line, or its standard input, or both joined by `with`). "
        "An item that is a prefix operator needs an example in which it begins the "
        "input or a quoted value of it, "
        "not only one where it follows another operator",
        kind=Kind.PROBE_LIST,
    )


def _section_finding(section: Section) -> Finding:
    ids = section.units
    span = f"{ids[0]} to {ids[-1]}" if ids else section.id
    return Finding(
        f"the worked example {section.id} ({span}) cannot be run as a probe on its "
        f"own ({section.reason}): write an `example:` under a criterion that cites "
        "one of its lines",
        kind=Kind.PROBE_UNDERIVABLE,
    )


def _cites(shown: Sequence[Case], units: Sequence[str]) -> bool:
    return any(set(case.units) & set(units) for case in shown)


def findings(owed: Owed, reply: JsonObject) -> tuple[Finding, ...]:
    """Return what the reply's definition still owes the probes (see the module docstring)."""
    shown = cases(reply)
    found = [
        _arrow_finding(arrow)
        for arrow in owed.arrows
        if not any(carries_arrow(case, arrow) for case in shown)
    ]
    found += [
        Finding(f"{item.reason}: no probe can be run for it", True, Kind.PROBE_UNDERIVABLE)
        for item in owed.loose
    ]
    for unit in owed.lists:
        if missing := untested_items(unit, shown):
            found.append(_list_finding(unit, missing))
    found += [
        _section_finding(section) for section in owed.sections if not _cites(shown, section.units)
    ]
    return tuple(found)
