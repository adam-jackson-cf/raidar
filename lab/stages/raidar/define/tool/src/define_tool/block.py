"""The line-keyed block the intake builds its definition with (``define put``).

One item per line, ``key: value``, the value verbatim to the end of the line:
no quoting, escaping, indentation or nesting, so a value that is itself JSON,
markdown or code needs nothing done to it. A line that opens an item
(``feature F1: Greeting``) is followed by the lines that belong to it
(``outcome: ...``) in the order written::

    title: <the end state in a few words>
    objective: <what the change achieves and why>
    named: <a feature, operation, endpoint, option, control or section the issue names>
    from: issue U3 U4 | code path:line | tests path:line | none
    feature F2: <name>
    outcome: <what it does>
    risk: requires review
    ac F2.AC1: <the criterion, in your words>
    source: U3 U4
    aspect: <situation> -> <outcome>
    example: <input> -> <expected>
    kind: regression
    decision 1: <text>
    rung: 1 to 5
    source: U3
    ref: path:line
    reverse: <how to reverse an assumption>
    risk: requires review
    retire: <test id as the runner names it>
    source: U3
    reason: <why it stops passing>
    question F3: <what blocks the feature>
    kind: objective_unclear | feature_conflict | missing_input
    proposal: <one answer>
    option: <another answer>
    reply Q1.1: accepted | overridden | answered | unclear | unanswered
    quote: <the reply text it rests on>

``parse`` reads a block into ``Item`` objects, each holding exactly the fields
of the matching part of ``schemas.INTAKE_REPLY_SCHEMA``; a line it cannot read
is a ``Kind.GRAMMAR`` finding (``F2.AC1 line 5: ...``) and the items around it
are kept. ``show`` writes items back in the same grammar. A criterion belongs
to the feature its id names (``F2.AC1``), else to the feature opened before it
in the block.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

from . import aspects
from .aspects import Finding
from .core import BEHAVIOUR_FROM_KEY, JsonObject, rows, strings, text
from .intakecheck import (
    FEATURE_SOURCES,
    QUESTION_KINDS,
    REPLY_OUTCOMES,
)
from .kinds import Kind

TITLE = "title"
OBJECTIVE = "objective"
NAMED = "named"
FEATURE = "feature"
AC = "ac"
DECISION = "decision"
RETIRE = "retire"
QUESTION = "question"
REPLY = "reply"
# An item opens with its kind alone, or with its kind and one id.
SINGLE = (TITLE, OBJECTIVE, NAMED, RETIRE)
IDENTIFIED = (FEATURE, AC, DECISION, QUESTION, REPLY)
# The lines that belong to an item, by its kind.
SUBKEYS: dict[str, tuple[str, ...]] = {
    TITLE: (),
    OBJECTIVE: (),
    NAMED: ("from",),
    FEATURE: ("outcome", "risk"),
    AC: ("source", "aspect", "example", "kind"),
    DECISION: ("rung", "source", "ref", "reverse", "risk"),
    RETIRE: ("source", "reason"),
    QUESTION: ("kind", "proposal", "option"),
    REPLY: ("quote",),
}
RISKS = ("", "requires review")
RUNGS = ("1", "2", "3", "4", "5")
CRITERION_KINDS = ("behaviour", aspects.REGRESSION)
_LINE = re.compile(r"^\s*(?P<head>[^:]*?)\s*:\s?(?P<value>.*?)\s*$")
_CRITERION_OF = re.compile(r"(?P<feature>F\d+)\.AC\d+")
# A decision's number: ASCII digits only (``str.isdigit`` takes ``²`` too, which ``int`` refuses).
NUMBER = re.compile(r"[0-9]{1,6}")


@dataclass(frozen=True)
class Item:
    """One item of the definition: its kind, its identity within the kind and its fields.

    ``data`` holds the fields of the matching object of the reply
    (``schemas.INTAKE_REPLY_SCHEMA``); ``owner`` is a criterion's feature id.
    A title or an objective is the item ``{"text": ...}``. A question is
    identified by the feature it blocks and its kind (``question_key``), so
    rewording it replaces it.
    """

    kind: str
    key: str
    data: JsonObject
    owner: str = ""
    # The line of the block that opened the item (0 for one read from a draft).
    line: int = field(default=0, compare=False)

    @property
    def ref(self) -> str:
        """What ``define drop`` names the item by: its id, or its kind and identity."""
        if self.kind in (TITLE, OBJECTIVE):
            return self.kind
        if self.kind in (FEATURE, AC, REPLY):
            return self.key
        return f"{self.kind} {self.key}"


def question_key(feature: str, kind: str) -> str:
    """Return the identity of a question: the feature it blocks and its kind, spacing aside."""
    return " ".join(f"{feature} {kind}".split())


@dataclass(frozen=True)
class Parsed:
    """A block read: the items it holds, and what could not be read."""

    items: tuple[Item, ...]
    findings: tuple[Finding, ...]


@dataclass
class _Open:
    """The item being read: its opening line and the lines under it."""

    kind: str
    arg: str
    value: str
    line: int
    feature: str
    subs: list[tuple[int, str, str]] = field(default_factory=list)
    problems: list[tuple[int, str]] = field(default_factory=list)
    fatal: bool = False

    @property
    def label(self) -> str:
        if self.kind in (NAMED, RETIRE):
            return f"{self.kind} `{self.value}`"
        if self.kind in (TITLE, OBJECTIVE, FEATURE, AC):
            return self.arg or self.kind
        return f"{self.kind} {self.arg}"

    def refuse(self, line: int, message: str) -> None:
        """Note a fault that leaves the item unusable."""
        self.problems.append((line, message))
        self.fatal = True

    def note(self, line: int, message: str) -> None:
        """Note a fault in one line; the item is still built without it."""
        self.problems.append((line, message))

    def last(self, key: str) -> tuple[int, str] | None:
        found = [(line, value) for line, name, value in self.subs if name == key]
        return found[-1] if found else None

    def every(self, key: str) -> list[tuple[int, str]]:
        return [(line, value) for line, name, value in self.subs if name == key]

    def one(self, key: str) -> str:
        found = self.last(key)
        return found[1] if found else ""

    def listed(self, key: str) -> list[str]:
        return [value for _, value in self.every(key) if value]

    def choice(self, key: str, allowed: Sequence[str], default: str = "") -> str:
        """Return the value of ``key`` when it is one of ``allowed``, else ``default`` and a
        note."""
        found = self.last(key)
        if found is None:
            return default
        if found[1] in allowed:
            return found[1]
        shown = " | ".join(f"`{item}`" for item in allowed if item)
        self.note(found[0], f"`{key}:` is `{found[1]}`, not one of {shown}")
        return default


Builder = Callable[[_Open], Item | None]


def _split(o: _Open, key: str) -> list[JsonObject]:
    """Read every ``aspect:`` or ``example:`` line of a criterion into its two sides."""
    sides = ("situation", "outcome") if key == "aspect" else ("input", "expected")
    found: list[JsonObject] = []
    for line, value in o.every(key):
        pair = aspects.split_arrow(value)
        if pair is None:
            o.note(
                line,
                f"`{key}:` needs one `->` outside backticks between its two sides "
                f"(`<{sides[0]}> {aspects.ARROW} <{sides[1]}>`)",
            )
            continue
        found.append({sides[0]: pair[0], sides[1]: pair[1]})
    return found


def _title(o: _Open) -> Item:
    return Item(o.kind, o.kind, {"text": o.value})


def _named(o: _Open) -> Item | None:
    found = o.last("from")
    if found is None:
        o.refuse(
            o.line,
            "needs a `from:` line (`issue U3 U4`, `code path:line`, `tests path:line` or `none`)",
        )
        return None
    source, _, where = found[1].partition(" ")
    if source not in FEATURE_SOURCES:
        shown = " | ".join(f"`{item}`" for item in FEATURE_SOURCES)
        o.refuse(found[0], f"`from:` starts with `{source}`, not one of {shown}")
        return None
    key = " ".join(o.value.casefold().split())
    data: JsonObject = {
        "name": o.value,
        BEHAVIOUR_FROM_KEY: source,
        "evidence": where.strip(),
    }
    return Item(NAMED, key, data)


def _feature(o: _Open) -> Item:
    data: JsonObject = {
        "id": o.arg,
        "name": o.value,
        "outcome": o.one("outcome"),
        "risk": o.choice("risk", RISKS),
    }
    return Item(FEATURE, o.arg, data)


def _owner(o: _Open) -> str:
    """Return the feature a criterion belongs to: the one its id names, else the one opened before
    it."""
    named = _CRITERION_OF.fullmatch(o.arg)
    return named["feature"] if named else o.feature


def _criterion(o: _Open) -> Item | None:
    owner = _owner(o)
    if not owner:
        o.refuse(
            o.line,
            f"`{o.arg}` names no feature: write its id as `F<n>.AC<n>`, or put it "
            "after the `feature` line it belongs to",
        )
        return None
    kind = o.choice("kind", CRITERION_KINDS, CRITERION_KINDS[0])
    data: JsonObject = {
        "id": o.arg,
        "text": o.value,
        "sources": o.listed("source"),
        "kind": kind,
        "aspects": _split(o, "aspect"),
        "examples": _split(o, "example"),
    }
    return Item(AC, o.arg, data, owner)


def _decision(o: _Open) -> Item | None:
    if not NUMBER.fullmatch(o.arg):
        o.refuse(o.line, f"`{o.arg}` is not a number: write `decision 1: <text>`")
        return None
    rung = o.choice("rung", RUNGS)
    if not rung:
        o.refuse(o.line, "needs a `rung:` line, `1` to `5`")
        return None
    data: JsonObject = {
        "text": o.value,
        "rung": rung,
        "sources": o.listed("source"),
        "refs": o.listed("ref"),
        "reverse": o.one("reverse"),
        "risk": o.choice("risk", RISKS),
    }
    return Item(DECISION, str(int(o.arg)), data)


def _retirement(o: _Open) -> Item:
    data: JsonObject = {
        "test": o.value,
        "sources": o.listed("source"),
        "reason": o.one("reason"),
    }
    return Item(RETIRE, o.value, data)


def _question(o: _Open) -> Item | None:
    kind = o.choice("kind", QUESTION_KINDS)
    if not kind:
        o.refuse(
            o.line,
            "needs a `kind:` line" if o.last("kind") is None else "needs a valid `kind:`",
        )
        return None
    data: JsonObject = {
        "feature": o.arg,
        "kind": kind,
        "question": o.value,
        "proposal": o.one("proposal"),
        "options": o.listed("option"),
    }
    return Item(QUESTION, question_key(o.arg, kind), data)


def _reply(o: _Open) -> Item | None:
    if o.value not in REPLY_OUTCOMES:
        shown = " | ".join(f"`{item}`" for item in REPLY_OUTCOMES)
        o.refuse(o.line, f"`{o.value}` is not one of {shown}")
        return None
    data: JsonObject = {"question": o.arg, "outcome": o.value, "quote": o.one("quote")}
    return Item(REPLY, o.arg, data)


BUILDERS: dict[str, Builder] = {
    TITLE: _title,
    OBJECTIVE: _title,
    NAMED: _named,
    FEATURE: _feature,
    AC: _criterion,
    DECISION: _decision,
    RETIRE: _retirement,
    QUESTION: _question,
    REPLY: _reply,
}


class _Reader:
    """Reads a block line by line into items and findings."""

    def __init__(self) -> None:
        self.items: list[Item] = []
        self.findings: list[Finding] = []
        self.open: _Open | None = None
        self.feature = ""

    def read(self, block: str) -> Parsed:
        for number, raw in enumerate(block.splitlines(), start=1):
            if raw.strip():
                self._line(number, raw)
        self._close()
        counts = Counter(item.ref for item in self.items)
        self.findings += [
            Finding(
                f"`{ref}` is given {number} times in this block: the last one is kept",
                kind=Kind.GRAMMAR,
            )
            for ref, number in counts.items()
            if number > 1
        ]
        return Parsed(tuple(self.items), tuple(self.findings))

    def _bad(self, number: int, message: str) -> None:
        self.findings.append(Finding(f"line {number}: {message}", kind=Kind.GRAMMAR))

    def _line(self, number: int, raw: str) -> None:
        found = _LINE.match(raw)
        words = found["head"].split() if found else []
        if found is None or not words:
            self._bad(number, f"`{raw.strip()[:60]}` is not a `key: value` line")
            return
        key, args, value = words[0], words[1:], found["value"].strip()
        if key in IDENTIFIED:
            if len(args) == 1 or (key == QUESTION and args):
                self._begin(number, key, " ".join(args), value)
            else:
                self._close()
                self._bad(
                    number,
                    f"`{key}` takes an id before the colon (`{key} <id>: <text>`)",
                )
        elif args:
            self._bad(number, f"unknown key `{' '.join(words)}`")
        elif key in SINGLE:
            self._begin(number, key, "", value)
        else:
            self._under(number, key, value)

    def _begin(self, number: int, kind: str, arg: str, value: str) -> None:
        self._close()
        self.open = _Open(kind, arg, value, number, self.feature)
        if kind == FEATURE:
            self.feature = arg
        if not value:
            self.open.refuse(number, "has no text after the colon")

    def _under(self, number: int, key: str, value: str) -> None:
        if not any(key in names for names in SUBKEYS.values()):
            self._bad(number, f"unknown key `{key}`")
        elif self.open is None:
            self._bad(number, f"`{key}:` comes before the item it belongs to")
        elif key not in SUBKEYS[self.open.kind]:
            taken = ", ".join(f"`{name}`" for name in SUBKEYS[self.open.kind])
            self.open.note(
                number,
                f"`{key}:` is not a line of {self.open.kind}"
                f"{'; it takes ' + taken if taken else ''}",
            )
        else:
            self.open.subs.append((number, key, value))

    def _close(self) -> None:
        if self.open is None:
            return
        o, self.open = self.open, None
        item = None if o.fatal else BUILDERS[o.kind](o)
        if item is not None and not o.fatal:
            self.items.append(replace(item, line=o.line))
        self.findings += [
            Finding(f"{o.label} line {line}: {message}", kind=Kind.GRAMMAR)
            for line, message in o.problems
        ]


def parse(block: str) -> Parsed:
    """Read a ``define put`` block; see the module docstring."""
    return _Reader().read(block)


# Writing ----------------------------------------------------------------------


def _list(key: str, values: Iterable[str]) -> list[str]:
    return [f"{key}: {value}" for value in values if value]


def _pairs(key: str, pairs: Sequence[JsonObject], left: str, right: str) -> list[str]:
    return [f"{key}: {text(pair, left)} {aspects.ARROW} {text(pair, right)}" for pair in pairs]


def _lines_named(item: Item) -> list[str]:
    data = item.data
    where = f"{text(data, BEHAVIOUR_FROM_KEY)} {text(data, 'evidence')}".strip()
    return [f"named: {text(data, 'name')}", f"from: {where}"]


def _lines_feature(item: Item) -> list[str]:
    data = item.data
    lines = [f"feature {item.key}: {text(data, 'name')}"]
    lines.append(f"outcome: {text(data, 'outcome')}")
    return lines + _list("risk", [text(data, "risk")])


def _lines_criterion(item: Item) -> list[str]:
    data = item.data
    lines = [f"ac {item.key}: {text(data, 'text')}"]
    lines += _list("source", strings(data, "sources"))
    lines += _pairs("aspect", rows(data, "aspects"), "situation", "outcome")
    lines += _pairs("example", rows(data, "examples"), "input", "expected")
    return lines + (
        [f"kind: {aspects.REGRESSION}"] if text(data, "kind") == aspects.REGRESSION else []
    )


def _lines_decision(item: Item) -> list[str]:
    data = item.data
    lines = [
        f"decision {item.key}: {text(data, 'text')}",
        f"rung: {text(data, 'rung')}",
    ]
    lines += _list("source", strings(data, "sources"))
    lines += _list("ref", strings(data, "refs"))
    lines += _list("reverse", [text(data, "reverse")])
    return lines + _list("risk", [text(data, "risk")])


def _lines_retirement(item: Item) -> list[str]:
    data = item.data
    lines = [f"retire: {text(data, 'test')}"]
    lines += _list("source", strings(data, "sources"))
    return lines + _list("reason", [text(data, "reason")])


def _lines_question(item: Item) -> list[str]:
    data = item.data
    lines = [
        f"question {text(data, 'feature')}: {text(data, 'question')}",
        f"kind: {text(data, 'kind')}",
        f"proposal: {text(data, 'proposal')}",
    ]
    return lines + _list("option", strings(data, "options"))


def _lines_reply(item: Item) -> list[str]:
    data = item.data
    lines = [f"reply {item.key}: {text(data, 'outcome')}"]
    return lines + _list("quote", [text(data, "quote")])


def _lines_text(item: Item) -> list[str]:
    return [f"{item.kind}: {text(item.data, 'text')}"]


WRITERS: dict[str, Callable[[Item], list[str]]] = {
    TITLE: _lines_text,
    OBJECTIVE: _lines_text,
    NAMED: _lines_named,
    FEATURE: _lines_feature,
    AC: _lines_criterion,
    DECISION: _lines_decision,
    RETIRE: _lines_retirement,
    QUESTION: _lines_question,
    REPLY: _lines_reply,
}


def show(items: Sequence[Item]) -> str:
    """Write items in the block grammar: what ``parse`` reads back as the same items."""
    lines = [line for item in items for line in WRITERS[item.kind](item)]
    return "".join(f"{line}\n" for line in lines)
