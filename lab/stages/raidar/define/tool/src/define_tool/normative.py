"""The issue's normative sentences, and which units a ``Source:`` quote shares words with.

Each statement of the issue is cut into a unit the intake cites by id
(``statements``), and a quote is matched back to the units it shares a word-run
with (``sharing``).

**Units.** The issue is read as markdown and cut into units: each sentence of a
paragraph, of a list item (each with its indented continuation lines) and of a
table's body row. Fenced code is set aside whichever mark opens it, backticks
or tildes (``sections.Fences``). Never units: HTML comments, fenced code blocks, headings,
horizontal rules, a table's header and separator rows, a line that is only
emphasis (``**Fields:**``, ``*Input*``) and a lead-in, a unit ending with
``:`` directly before a list, a table or a code block (it introduces what
follows, which is read on its own). A sentence ends at ``.``, ``!`` or ``?``
followed by space and a capital, a digit, a code span or a quote, except inside
a code span or after a common abbreviation (``e.g.``, ``i.e.``, ``etc.``,
``vs.``). ``statements`` also gives the lead-ins (flagged) and ``code_lines`` the fenced
code lines; neither changes ``units``.

**Normative.** A unit is normative when it states a requirement or a defined
behaviour, by any of these signals (``signals`` names them):

- ``keyword``: a requirement keyword (RFC 2119 and plain-English
  equivalents): must, shall, should, required, requires, need to, has to,
  mandatory, cannot, never, always, only, ensure, allowed, forbidden,
  reserved, optional, default, …;
- ``imperative``: it opens with an instruction (``Add …``, ``Return …``,
  ``Do not …``): a base-form verb from ``_ORDER``;
- ``conditional``: it states what happens in a case (if, when, whenever,
  unless, otherwise, on success, on error, …);
- ``behaviour``: it says what the system does, with a behaviour verb in any
  form (returns, is ignored, rejects, sorted, yields, …; ``_BEHAVIOUR``);
- ``definition``: it defines a named element: it opens with a code span, a
  quoted or bold term (and any parenthetical) followed by ``:``, ``-``,
  ``–``, ``—`` or ``=`` and a definition (`` `NO_SUCH_FLAG` - flag not
  found``), or with a short label and a colon before a code span
  (``Error codes: `BAD_FLAG` …``), or it is a table's body row;
- ``contract``: it says what a code-named element is (a code span and ``is``,
  ``are`` or ``be``: `` `keys` are case-sensitive names``).

Unit rules 2 (``unitrules``) add two more, and every function here takes the
rule set it reads by:

- ``outcome``: it states an outcome with an arrow, ``X → Y`` or ``X -> Y``,
  outside a code span (``Unknown flags → `BAD_FLAG` ``);
- ``enumeration``: it is a labelled list of backticked items
  (``labelled.labelled_list``, read over the unit, so the run-health check C5
  reads the same lists: ``enumerations``).

What is left is context: motivation, background and narrative sentences
that name no element and state no behaviour.

**Quoted.** A unit is quoted when a ``Source:`` quote shares at least one whole
word-run with it: the quote lies inside the unit, the unit inside the quote, or
the quote runs across the unit's start or end. Quotes and units are compared
in ``evidence.normal`` form with quote marks set aside, as ``Source:`` quotes
are checked against the issue (``evidence.verbatim_problem``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from . import (
    enumerated,
    evidence,
    issuetext,
    labelled,
    unitrules,
)
from .sections import Fences
from .unitrules import UnitRules

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_TEXT = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:\s+(?P<text>.*?))?\s*#*\s*$")
_RULE = re.compile(r"^ {0,3}(?:[-*_]\s*){3,}$")
_ITEM = re.compile(r"^(?P<indent>\s*)(?:[-*+]|\d+[.)])\s+(?P<text>.*\S)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
# A unit that is only emphasis or a code span: a label, not a statement.
_LABEL = re.compile(r"^(?:[*_]{1,3}[^*_]+[*_]{1,3}|`[^`]+`)\s*:?\s*$")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl.")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'(\[*_\u201c])")
_WORD = re.compile(r"\w+")
_CODE_SPAN = re.compile(r"`+[^`]*`+")

_KEYWORD = re.compile(
    r"\b(?:must|shall|should|required|requires?|needs?\s+to|has\s+to|have\s+to"
    r"|mandatory|may\s+not|cannot|can't|can\s+not|never|always|only|ensures?"
    r"|forbidden|prohibited|(?:not\s+|dis)?allowed|reserved|optional|defaults?"
    r"|invalid|valid|exactly|at\s+(?:least|most)|no\s+more\s+than|unique"
    r"|case-(?:in)?sensitive)\b",
    re.IGNORECASE,
)
# The base-form verbs an instruction opens with (``imperative``).
_ORDER = re.compile(
    r"^(?:"
    r"add|extend|support|implement|return|reject|accept|ignore|validate|"
    r"use|allow|make|ensure|emit|print|write|raise|treat|default|keep|"
    r"preserve|sort|include|exclude|handle|create|remove|delete|update|"
    r"store|read|parse|compute|output|report|respond|fail|exit|log|"
    r"apply|convert|provide|expose|introduce|rename|replace|change|"
    r"modify|generate|render|format|normalize|truncate|round|count|"
    r"require|enforce|prevent|avoid|skip|retry|stop|limit|cap|map|merge|"
    r"split|join|strip|trim|escape|encode|decode|serialize|load|save|"
    r"persist|record|track|display|show|list|group|filter|aggregate|"
    r"calculate|check|verify|detect|resolve|evaluate|bind|call|invoke|"
    r"override|deduplicate|reuse|wrap|expand|build|scan|honor|honour|"
    r"drop|copy|enable|disable|get|set|recreate|run|send|open|close|"
    r"start|define|specify|mark|register|migrate|query|fetch|pause|wait|"
    r"listen|serve|execute|recurse"
    r")\b",
    re.IGNORECASE,
)
_IMPERATIVE = re.compile(r"^(?:do\s+not|don't|never|always|please)\b", re.IGNORECASE)
_CONDITIONAL = re.compile(
    r"\b(?:if|when|whenever|unless|otherwise|in\s+which\s+case|on\s+success"
    r"|on\s+failure|on\s+error|in\s+case\s+of|instead|until|before|after)\b",
    re.IGNORECASE,
)
# The verbs, in any form, that say what the system does (``behaviour``).
_BEHAVIOUR = re.compile(
    r"\b(?:"
    r"return|yield|produce|output|emit|print|write|raise|throw|fail|"
    r"exit|ignore|accept|reject|default|match|treat|sort|order|preserve|"
    r"round|truncate|validate|map|convert|compute|count|append|"
    r"overwrite|skip|stop|retry|abort|respond|log|report|store|persist|"
    r"load|read|parse|serialize|format|render|encode|decode|normalize|"
    r"resolve|evaluate|apply|bind|call|invoke|override|inherit|expire|"
    r"limit|cap|clamp|escape|strip|trim|split|join|merge|replace|rename|"
    r"delete|remove|create|update|insert|lock|contain|include|exclude|"
    r"use|require|allow|keep|fall|handle|generate|display|show|list|"
    r"group|filter|aggregate|calculate|check|verify|detect|reuse|wrap|"
    r"expand|mean|become|stay|remain|pass|locate|trigger|execute|run|"
    r"send|receive|scan|wait|pause|listen"
    r")(?:s|es|ed|d|ing|ning|ped|ted)?\b",
    re.IGNORECASE,
)
_DEFINITION = re.compile(
    r"^(?:(?:`[^`]+`|\"[^\"]+\"|\u201c[^\u201d]+\u201d|\*\*[^*]+\*\*)"
    r"(?:\s*\([^()]*\))?\s*(?::|-|\u2013|\u2014|=)\s*\S"
    r"|[^:`]{1,40}:\s*`)"
)
_CONTRACT = re.compile(r"`[^`]+`.*\b(?:is|are|be|isn't|aren't)\b", re.IGNORECASE)
# ``X → Y``: an arrow with something on each side (``outcome``, rules 2).
_ARROW = re.compile(r"\S\s*(?:\u2192|->)\s*\S")


@dataclass(frozen=True)
class Unit:
    """One statement of the issue: its text as written and why it is normative."""

    text: str
    signals: tuple[str, ...]

    @property
    def normative(self) -> bool:
        return bool(self.signals)


def _pattern_signals(said: str) -> list[str]:
    """Return the signals the sentence's own words carry, in the order they are listed."""
    found: list[str] = []
    if _KEYWORD.search(said):
        found.append("keyword")
    # A sentence opening with a code span names an element; it is not an order.
    opening = said.lstrip("*_\"'")
    if _IMPERATIVE.match(opening) or _ORDER.match(opening):
        found.append("imperative")
    if _CONDITIONAL.search(said):
        found.append("conditional")
    if _BEHAVIOUR.search(said):
        found.append("behaviour")
    if _DEFINITION.match(said):
        found.append("definition")
    if _CONTRACT.search(said):
        found.append("contract")
    return found


def signals(sentence: str, rules: UnitRules) -> tuple[str, ...]:
    """Return the normative signals a sentence carries under ``rules`` (see the module
    docstring)."""
    said = sentence.strip()
    found = _pattern_signals(said)
    if rules.arrow_outcome and _ARROW.search(issuetext.blank_code(said)):
        found.append("outcome")
    if rules.lists and labelled.labelled_list(said) is not None:
        found.append("enumeration")
    return tuple(found)


def _sentences(text: str) -> list[str]:
    """Split one block's text into sentences (see the module docstring)."""
    parts: list[str] = []
    start = 0
    spans = [found.span() for found in _CODE_SPAN.finditer(text)]
    for match in _SENTENCE_END.finditer(text):
        before = text[start : match.start()]
        inside = any(first < match.start() < last for first, last in spans)
        if inside or before.rstrip().casefold().endswith(_ABBREVIATIONS):
            continue
        parts.append(before)
        start = match.end()
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


@dataclass
class _Blocks:
    """Cut issue lines into blocks: paragraphs, list items and table rows."""

    blocks: list[tuple[str, str]]
    current: list[str]
    kind: str = ""

    def flush(self) -> None:
        if self.current:
            self.blocks.append((self.kind, " ".join(self.current)))
        self.current, self.kind = [], ""

    def boundary(self, kind: str) -> None:
        """End the open block; ``kind`` marks what follows it (list, table, code)."""
        self.flush()
        self.blocks.append((kind, ""))

    def add(self, kind: str, text: str) -> None:
        if kind != "continue":
            self.flush()
            self.kind = kind
        self.current.append(text)


def _closes_block(raw: str, line: str, inside_fence: bool) -> bool:
    """Whether the line ends the open block without starting one."""
    return bool(inside_fence or _HEADING_TEXT.match(raw) or not line or _RULE.match(raw))


def _wraps(cut: _Blocks, raw: str) -> bool:
    """Whether the line continues the open paragraph or item.

    An indented line under a paragraph or an item wraps it (CommonMark).
    """
    return bool(cut.current) and (cut.kind in ("item", "paragraph") or raw[:1] not in (" ", "\t"))


def _blocks(issue: str) -> list[tuple[str, str]]:
    """Return the issue's text blocks as ``(kind, text)`` (boundaries have empty text)."""
    cut = _Blocks([], [])
    fences = Fences()
    for raw in _COMMENT.sub("", issue).splitlines():
        if fences.delimits(raw):
            if fences.inside:
                cut.boundary("code")
            continue
        line = raw.strip()
        if _closes_block(raw, line, fences.inside):
            cut.flush()
        elif _TABLE_ROW.match(raw):
            _table_row(cut, line)
        elif item := _ITEM.match(raw):
            cut.boundary("list")
            cut.add("item", item.group("text"))
        elif _wraps(cut, raw):
            cut.add("continue", line)
        else:
            cut.add("paragraph", line)
    cut.flush()
    return cut.blocks


def _table_row(cut: _Blocks, line: str) -> None:
    """Add a table body row; the header row and the separator are not units."""
    if _TABLE_SEPARATOR.match(line):
        # The row before the separator was the header: drop it.
        if cut.kind == "row" and cut.current:
            cut.current = []
        cut.kind = "row"
        return
    if cut.kind != "row":
        cut.boundary("table")
    else:
        cut.flush()
    cells = (cell.strip() for cell in line.strip().strip("|").split("|"))
    cut.add("row", " | ".join(cells))


def _lead_in(blocks: Sequence[tuple[str, str]], index: int) -> bool:
    """Whether block ``index`` ends with ``:`` right before a list, table or code."""
    text = blocks[index][1].rstrip(" *_")
    following = next((b for b in blocks[index + 1 :] if b[1] or b[0]), None)
    return (
        text.endswith(":")
        and following is not None
        and following[0] in ("list", "table", "code", "item")
    )


def statements(issue: str, rules: UnitRules) -> list[tuple[Unit, bool]]:
    """Return every statement of the issue, in order, with whether it is a lead-in.

    The same walk as ``units``, except that a lead-in block (a unit ending with
    ``:`` right before a list, a table or a code block) is not skipped: each of
    its sentences, or its row, comes out with ``True``. The intake cites a
    lead-in (``capability.define.compact``); it is never a required unit.
    """
    blocks = _blocks(issue)
    found: list[tuple[Unit, bool]] = []
    for index, (kind, text) in enumerate(blocks):
        if not text or _LABEL.match(text):
            continue
        lead = _lead_in(blocks, index)
        if kind == "row":
            # A table's body row defines the element its first cell names.
            said = signals(text, rules)
            found.append(
                (
                    Unit(text, tuple(dict.fromkeys((*said, "definition")))),
                    lead,
                )
            )
            continue
        for sentence in _sentences(text):
            if _LABEL.match(sentence) or not _WORD.search(sentence):
                continue
            found.append((Unit(sentence, signals(sentence, rules)), lead))
    return found


def units(issue: str, rules: UnitRules) -> list[Unit]:
    """Return every unit of the issue, in order, with its normative signals."""
    return [unit for unit, lead in statements(issue, rules) if not lead]


def code_lines(issue: str) -> list[str]:
    """Return every non-blank line inside a fenced code block, stripped, in order."""
    found: list[str] = []
    fences = Fences()
    for raw in _COMMENT.sub("", issue).splitlines():
        if fences.delimits(raw):
            continue
        if fences.inside and raw.strip():
            found.append(raw.strip())
    return found


def enumerations(issue: str) -> list[enumerated.Listing]:
    """Return the labelled lists the issue's units are, in order (rules 2's ``enumeration``
    units)."""
    return [
        listing
        for unit in units(issue, unitrules.V2)
        if (listing := labelled.labelled_list(unit.text)) is not None
    ]


def normative(issue: str, rules: UnitRules) -> list[Unit]:
    """Return the issue's normative units, in order."""
    return [unit for unit in units(issue, rules) if unit.normative]


def _occurrences(said: str, flat: str) -> Iterator[tuple[int, int]]:
    """Yield the spans of ``said`` in ``flat`` that start and end on word boundaries."""
    start = flat.find(said) if said else -1
    while start >= 0:
        end = start + len(said)
        before = start > 0 and flat[start - 1].isalnum() and said[0].isalnum()
        after = end < len(flat) and flat[end].isalnum() and said[-1].isalnum()
        if not (before or after):
            yield start, end
        start = flat.find(said, start + 1)


@dataclass(frozen=True)
class _Flat:
    """The issue's units in ``evidence.normal`` form, joined, with the span each takes."""

    parts: list[str]
    spans: list[tuple[int, int]]
    text: str

    @classmethod
    def of(cls, every: Sequence[Unit]) -> _Flat:
        parts, spans, at = [], [], 0
        for unit in every:
            flat = evidence.normal(unit.text, unquoted=True)
            spans.append((at, at + len(flat)))
            parts.append(flat)
            at += len(flat) + 1
        return cls(parts, spans, " ".join(parts))


def _shared(quote: str, flat: _Flat) -> set[int]:
    """Return the units ``quote`` shares a whole word-run with.

    A quote inside the unit, running across its start or end, or holding all of it.
    """
    found: set[int] = set()
    places = list(_occurrences(quote, flat.text))
    for index, (part, (first, last)) in enumerate(zip(flat.parts, flat.spans, strict=True)):
        if (part and next(_occurrences(part, quote), None) is not None) or any(
            start < last and first < end for start, end in places
        ):
            found.add(index)
    return found


def sharing(quote: str, every: Sequence[Unit]) -> list[int]:
    """Return the indexes of the units ``quote`` shares a whole word-run with, in order."""
    said = evidence.normal(quote, unquoted=True)
    if not said:
        return []
    return sorted(_shared(said, _Flat.of(every)))
