"""The feature definition a define-build-verify intake writes.

``.raidar/feature-definition.md`` is plain markdown, written once by the
intake and never updated (no tables, no status fields)::

    # <title>

    ## Objective
    <what the change achieves and why>

    ## Features

    ### F1 <name>
    Outcome: <the user-visible outcome>
    Acceptance criteria:
    - <an observable criterion a test can prove>

    ## Decisions and assumptions
    - <decision or assumption> (source: <rung>: <where>)

The engine checks it minimally (``check``): the three sections are present,
each once, the objective says something, and every feature has an id, a name,
an outcome and at least one acceptance criterion. Everything else is the intake's.

An audited definition reads it ``traced``: every acceptance criterion
starts with its id, ``F<n>.AC<m>`` under feature ``F<n>``, and carries a
``Verification:`` line naming the automated test (or, rarely, the
deterministic check) that will prove it::

    Acceptance criteria:
    - F1.AC1: <an observable criterion a test can prove>
      Verification: <the test that will prove it>

``check(text, traced=True)`` also requires both. Read traced, an indented line
under a criterion continues it or its ``Verification:`` line (hard-wrapped),
and a criterion or ``Verification:`` line found after the list ended is an
error. Read untraced, the same text means what it always did.

An anchored definition reads it ``anchored`` (traced, and more): every
criterion quotes the issue words it derives from on one or more ``Source:``
lines, and ``check(text, anchored=True, source=…)`` checks every quote is the
issue's own words (``evidence.verbatim_problem``)::

    ### F1 <name>
    Outcome: <the user-visible outcome>
    Acceptance criteria:
    - F1.AC1: <an observable criterion a test can prove>
      Source: "<the issue's own words, exactly>"
      Verification: <the test that will prove it>

``define-build-verify`` reads it ``aspects`` (anchored, and more): under each
criterion, ``Aspect: <situation> -> <outcome>`` lines name what a test sets up
and asserts, ``Example: <input> -> <expected>`` pins a stated value, and ``Kind:
regression`` replaces both (``capability.define.aspects``).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from . import (
    aspects as aspect_lines,
)
from . import (
    evidence,
)
from . import (
    sections as md,
)
from .core import JsonObject
from .kinds import Kind
from .sections import Fences

FILE = ".raidar/feature-definition.md"
OBJECTIVE = "Objective"
FEATURES = "Features"
DECISIONS = "Decisions and assumptions"
# A decision that retires base tests says so, and names each in backticks.
_RETIRES = re.compile(r"\bretir(?:e|es|ed|ing)\b", re.IGNORECASE)
_TEST_ID = re.compile(r"`([^`\s]+\.py::[^`\n]+)`")
# A passage a decision quotes from the issue, in straight or curly double marks.
_QUOTED = re.compile(r"[\"\u201c]([^\"\u201c\u201d]+)[\"\u201d]")
SECTIONS = (OBJECTIVE, FEATURES, DECISIONS)
REQUIRES_REVIEW = "requires review"
_FEATURE = re.compile(r"^###\s+[*_`]*(?P<id>F?\d+|[^\s*_`:.]+)[*_`]*[:.]?\s*(?P<name>.*?)\s*$")
_FEATURE_ID = re.compile(r"F\d+")
# A dash that separates a feature's id from its name (`### F1 - Health`, `### F1 —`):
# a dash run standing alone, never the dashes a name opens with (`--top option`).
_NAME_SEPARATOR = re.compile(r"^[-—–]+(?:\s+|$)|\s+[-—–]+$")
# A label line, bold or not: `Outcome: …`, `**Outcome:** …`.
_OUTCOME = re.compile(r"^[*_]*outcome[*_]*\s*:[*_]*\s*(?P<text>.*?)\s*$", re.IGNORECASE)
# The label alone, or with a colon and the first criterion after it: a phrase
# that only starts with the words (a wrapped line) is no label.
_CRITERIA = re.compile(
    r"^[*_]*acceptance criteria(?:[*_]*\s*:[*_]*\s*(?P<text>.*?))?[*_]*\s*$",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+(?P<text>.*\S)\s*$")
# A traced criterion's id at the start of its bullet: `F1.AC1: …`, `**F1.AC1** …`.
_CRITERION_ID = re.compile(
    r"^[*_`]*(?P<id>F\d+\.AC\d+)[*_`]*\s*[:.)\-—–]?[*_`]*\s*(?P<text>.*?)\s*$"
)
# A traced criterion's verification line, bold or bulleted or not.
_VERIFICATION = re.compile(
    r"^(?:[-*+]\s+)?[*_]*verification[*_]*\s*:[*_]*\s*(?P<text>.*?)\s*$",
    re.IGNORECASE,
)
# What a `Verification:` line may not say: it names no test or check.
_NO_VERIFICATION = frozenset({"", "none", "n/a", "na", "tbd", "todo", "-"})
# Emphasis, brackets and sentence punctuation round such a value (``N/A.``).
_VALUE_EDGES = " .,;:!?*_`'\"()[]-\u2013\u2014"
# Read anchored: the labelled lines under a criterion besides its `Verification:` line.
_ANNOTATION = re.compile(
    r"^(?:[-*+]\s+)?[*_]*(?P<label>source)[*_]*\s*:[*_]*\s*(?P<text>.*?)\s*$",
    re.IGNORECASE,
)
# Read with aspects: `Aspect:`, `Example:` and `Kind:` lines too.
_ASPECTED = re.compile(
    r"^(?:[-*+]\s+)?[*_]*(?P<label>source|aspect|example|kind)[*_]*\s*:[*_]*\s*"
    r"(?P<text>.*?)\s*$",
    re.IGNORECASE,
)
SOURCE = "source"
# The fewest non-filler words a `Source:` quote holds (``evidence.FILLER``).
SOURCE_MIN_WORDS = 2
# Quote marks a `Source:` quote is wrapped in, by opening mark.
_QUOTE_MARKS = {'"': '"', "\u201c": "\u201d", "'": "'", "\u2018": "\u2019"}


@dataclass(frozen=True)
class Criterion:
    """A traced acceptance criterion: its id, text and verification line.

    Read anchored, also its ``Source:`` quotes. Read with aspects, also its
    ``Aspect:`` and ``Example:`` lines (``capability.define.aspects``) and its ``Kind:``.
    """

    id: str
    text: str
    verification: str = ""
    anchored: bool = False
    sources: tuple[str, ...] = ()
    aspected: bool = False
    aspects: tuple[aspect_lines.Aspect, ...] = ()
    examples: tuple[aspect_lines.Example, ...] = ()
    kind: str = ""

    def record(self) -> JsonObject:
        found: JsonObject = {
            "id": self.id,
            "criterion": self.text,
            "verification": self.verification,
        }
        if self.anchored:
            found["sources"] = list(self.sources)
        if self.aspected:
            found["kind"] = self.kind
            found["aspects"] = [item.record() for item in self.aspects]
            found["examples"] = [item.record() for item in self.examples]
        return found


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    outcome: str
    criteria: tuple[str, ...]
    # Read traced: each criterion with its id and verification line;
    # ``criteria`` then holds their text without the id.
    traced: tuple[Criterion, ...] = ()
    # Read traced: criterion or `Verification:` lines after the list ended.
    stranded: tuple[str, ...] = ()

    def record(self) -> JsonObject:
        """Return the feature as a run records it."""
        found: JsonObject = {
            "id": self.id,
            "name": self.name,
            "outcome": self.outcome,
            "criteria": list(self.criteria),
        }
        if self.traced:
            found["acceptance"] = [item.record() for item in self.traced]
        return found


@dataclass(frozen=True)
class FeatureDefinition:
    """A feature definition as read back."""

    title: str
    sections: Mapping[str, str]
    features: tuple[Feature, ...]
    decisions: tuple[str, ...]
    # Headings under `## Features` that are not `### F<n> <name>`.
    stray: tuple[str, ...] = ()
    # The required sections whose ``## `` heading appears more than once: only
    # the last one is read, so the definition is refused (``findings``).
    repeated: tuple[str, ...] = ()

    def requires_review(self) -> list[str]:
        """Return the decisions marked ``requires review``."""
        return [item for item in self.decisions if REQUIRES_REVIEW in item.casefold()]

    def acceptance(self) -> tuple[Criterion, ...]:
        """Return every traced acceptance criterion, in definition order."""
        return tuple(item for feature in self.features for item in feature.traced)


def retired_tests(decisions: Sequence[str], source: str) -> list[str]:
    """Return the tests the change retires, in decision order.

    A decision marked ``requires review`` that says it retires a test names
    each by its identifier in backticks (``retires `tests/test_x.py::test_y```)
    and quotes the issue's words that change or remove the behaviour that test
    asserts. It grants the exemption only when every quoted passage in it is
    verbatim from ``source`` (the issue; ``evidence.verbatim_problem``). The gate
    lets exactly these stop passing (``Verifier.retire``).
    """
    found: list[str] = []
    for item in decisions:
        if REQUIRES_REVIEW not in item.casefold() or not _RETIRES.search(item):
            continue
        quotes = _QUOTED.findall(item)
        if quotes and not any(evidence.verbatim_problem(quote, source, 2) for quote in quotes):
            found += [test.strip() for test in _TEST_ID.findall(item)]
    return list(dict.fromkeys(found))


def unquote(said: str) -> str:
    """Return a quote without the one pair of quote marks it is wrapped in."""
    said = said.strip()
    if said[-1:] in ".," and len(said) > 2 and said[-2] in _QUOTE_MARKS.values():
        said = said[:-1]
    close = _QUOTE_MARKS.get(said[:1])
    if close is not None and len(said) >= 2 and said.endswith(close):
        return said[1:-1].strip()
    return said


def _traced(
    criteria: list[str],
    verified: dict[int, str],
    notes: dict[int, list[list[str]]] | None = None,
    aspects: bool = False,
) -> tuple[Criterion, ...]:
    found = []
    for index, criterion in enumerate(criteria):
        said = _CRITERION_ID.match(criterion)
        ident, text = (said["id"], said["text"]) if said else ("", criterion)
        item = Criterion(ident, text, verified.get(index, ""))
        if notes is not None:
            item = _anchored(item, notes.get(index, []))
            if aspects:
                item = _aspected(item, notes.get(index, []))
        found.append(item)
    return tuple(found)


def _anchored(item: Criterion, notes: list[list[str]]) -> Criterion:
    """Return ``item`` with its ``Source:`` lines (``[label, text]`` each) read."""
    sources = [unquote(said) for raw, said in notes if raw.casefold() == SOURCE]
    return dataclasses.replace(item, anchored=True, sources=tuple(sources))


def _aspected(item: Criterion, notes: list[list[str]]) -> Criterion:
    """Return ``item`` with its `Aspect:`, `Example:` and `Kind:` lines read."""
    lines: dict[str, list[str]] = {}
    for raw, said in notes:
        lines.setdefault(raw.casefold(), []).append(said)
    kinds = lines.get(aspect_lines.KIND, [])
    return dataclasses.replace(
        item,
        aspected=True,
        aspects=aspect_lines.read_aspects(item.id, lines.get(aspect_lines.ASPECT, [])),
        examples=tuple(
            aspect_lines.read_example(said) for said in lines.get(aspect_lines.EXAMPLE, [])
        ),
        kind=aspect_lines.read_kind(kinds[-1]) if kinds else "",
    )


def _labelled(aspects: bool) -> re.Pattern[str]:
    """Return the pattern for the labelled lines a definition reads."""
    return _ASPECTED if aspects else _ANNOTATION


def _stranded(line: str, anchored: bool = False, aspects: bool = False) -> bool:
    """Whether a line outside the criteria list reads as a criterion or verification."""
    bullet = _BULLET.match(line)
    said = _CRITERION_ID.match(bullet.group("text") if bullet else line)
    if anchored and _labelled(aspects).match(line):
        return True
    return bool(said and said.group("text")) or bool(_VERIFICATION.match(line))


def _label(found: re.Match[str]) -> str:
    """Return an anchored line's label, spacing collapsed."""
    return " ".join(found.group("label").split())


@dataclass
class _Reader:
    """Read one feature's body, line by line (``traced``: the criteria).

    ``anchored`` also reads each criterion's labelled lines (``_ANNOTATION``).
    """

    traced: bool
    anchored: bool = False
    aspects: bool = False
    outcome: str = ""
    criteria: list[str] = field(default_factory=list)
    # Traced: the verification line under each criterion, by its index;
    # whether the last line read was one; lines stranded outside the list.
    verified: dict[int, str] = field(default_factory=dict)
    in_verification: bool = False
    stranded: list[str] = field(default_factory=list)
    listing: bool = False
    # Anchored: each criterion's labelled lines as ``[label, text]``, by its
    # index, and whether the last line read was one.
    notes: dict[int, list[list[str]]] = field(default_factory=dict)
    in_note: bool = False
    # The indentation of the line the last criterion started on.
    indent: int = 0

    def read(self, raw: str) -> None:
        line = raw.strip()
        if not line:
            return
        outcome, criteria = _OUTCOME.match(line), _CRITERIA.match(line)
        if (outcome or criteria) and self._wrapped(raw):
            # Indented under a criterion: its wrapped text, not a label.
            self._traced_item(raw, line)
        elif outcome or criteria:
            self._label(outcome, criteria, raw)
        else:
            self._body(raw, line)

    def _label(
        self, outcome: re.Match[str] | None, criteria: re.Match[str] | None, raw: str
    ) -> None:
        """Read an ``Outcome:`` or ``Acceptance criteria:`` label line."""
        if outcome:
            self.outcome, self.listing = outcome.group("text"), False
        elif criteria:
            self.listing = True
            if criteria.group("text"):
                self._add(criteria.group("text"), raw)

    def _body(self, raw: str, line: str) -> None:
        """Read a line that is no label: a criterion, a bullet under the list, or the end of it."""
        if self.traced and self.listing and self.criteria:
            self._traced_item(raw, line)
        elif self.listing and (bullet := _BULLET.match(line)):
            self._add(bullet.group("text"), raw)
        else:
            self._end(line)

    def _add(self, criterion: str, raw: str) -> None:
        """Start a criterion on the line ``raw``."""
        self.criteria.append(criterion)
        self.indent = len(raw) - len(raw.lstrip())

    def _wrapped(self, raw: str) -> bool:
        """Whether ``raw`` is indented deeper than the criterion it follows (traced)."""
        return (
            self.traced
            and self.listing
            and bool(self.criteria)
            and len(raw) - len(raw.lstrip()) > self.indent
        )

    @property
    def _annotation(self) -> re.Pattern[str]:
        return _labelled(self.aspects)

    def _traced_item(self, raw: str, line: str) -> None:
        """Read a traced line inside the criteria list.

        An indented line continues the criterion or its verification,
        hard-wrapped; any other line ends the list (``_end``).
        """
        at = len(self.criteria) - 1
        if said := _VERIFICATION.match(line):
            self.verified[at], self.in_verification = said.group("text"), True
            self.in_note = False
        elif self.anchored and (said := self._annotation.match(line)):
            self.notes.setdefault(at, []).append([_label(said), said.group("text")])
            self.in_note, self.in_verification = True, False
        elif bullet := _BULLET.match(line):
            self._add(bullet.group("text"), raw)
            self.in_verification = self.in_note = False
        elif raw[:1] in (" ", "\t") and self.in_verification:
            self.verified[at] = f"{self.verified[at]} {line}"
        elif raw[:1] in (" ", "\t") and self.in_note:
            note = self.notes[at][-1]
            note[1] = f"{note[1]} {line}".strip()
        elif raw[:1] in (" ", "\t"):
            self.criteria[at] = f"{self.criteria[at]} {line}"
        else:
            self._end(line)

    def _end(self, line: str) -> None:
        """End the list on ``line``; traced, keep it when it reads as a criterion."""
        self.listing = False
        if self.traced and _stranded(line, self.anchored, self.aspects):
            self.stranded.append(line)


def _feature(
    heading: str,
    body: list[str],
    traced: bool = False,
    anchored: bool = False,
    aspects: bool = False,
) -> Feature:
    found = _FEATURE.match(heading)
    ident = found.group("id") if found else ""
    name = _NAME_SEPARATOR.sub("", found.group("name")).strip() if found else ""
    reader = _Reader(traced or anchored, anchored, aspects if anchored else False)
    for raw in body:
        reader.read(raw)
    if not reader.traced:
        return Feature(ident, name, reader.outcome, tuple(reader.criteria))
    notes = reader.notes if anchored else None
    items = _traced(reader.criteria, reader.verified, notes, bool(reader.aspects))
    texts = tuple(item.text for item in items)
    return Feature(ident, name, reader.outcome, texts, items, tuple(reader.stranded))


def _blocks(body: str) -> list[tuple[str, list[str]]]:
    """Return each ``### `` heading under ``## Features`` with the lines that follow it."""
    blocks: list[tuple[str, list[str]]] = []
    fences = Fences()
    for line in body.splitlines():
        code = fences.delimits(line) or fences.inside
        if line.startswith("### ") and not code:
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def _features(
    body: str, traced: bool = False, anchored: bool = False, aspects: bool = False
) -> tuple[tuple[Feature, ...], tuple[str, ...]]:
    """Read every ``### `` block under ``## Features``; return features and strays."""
    blocks = _blocks(body)
    features = [_feature(heading, lines, traced, anchored, aspects) for heading, lines in blocks]
    stray = tuple(
        heading
        for (heading, _), feature in zip(blocks, features, strict=True)
        if not _FEATURE_ID.fullmatch(feature.id)
    )
    return tuple(f for f in features if _FEATURE_ID.fullmatch(f.id)), stray


def decision_lines(body: str) -> tuple[str, ...]:
    """Return the bullet items of a decisions section (or file), ``none`` left out."""
    found = [
        bullet.group("text")
        for line in body.splitlines()
        if (bullet := _BULLET.match(line.strip()))
    ]
    return tuple(item for item in found if item.casefold().rstrip(".") != "none")


def parse(
    text: str, traced: bool = False, anchored: bool = False, aspects: bool = False
) -> FeatureDefinition:
    """Read a feature definition back.

    ``traced``: the criterion ids and verifications; ``anchored``: those too,
    with its quotes; ``aspects`` (needs ``anchored``): those too, with each
    criterion's aspects, examples and kind.
    """
    head, sections = md.split(text)
    title = next((line[2:].strip() for line in head.splitlines() if line.startswith("# ")), "")
    named = {name.strip().casefold(): body for name, body in sections}
    by_name = {name: named[name.casefold()] for name in SECTIONS if name.casefold() in named}
    headings = [name.strip().casefold() for name, _ in sections]
    repeated = tuple(name for name in SECTIONS if headings.count(name.casefold()) > 1)
    features, stray = _features(by_name.get(FEATURES, ""), traced, anchored, aspects)
    return FeatureDefinition(
        title,
        by_name,
        features,
        decision_lines(by_name.get(DECISIONS, "")),
        stray,
        repeated,
    )


def _feature_errors(feature: Feature) -> list[str]:
    label = f"feature `{feature.id}`"
    errors = []
    if not feature.name:
        errors.append(f"{label} has no name: put `feature {feature.id}: <name>`")
    if not feature.outcome:
        errors.append(
            f"{label} has no outcome: put `outcome: <what it does>` under its `feature` line"
        )
    if not feature.criteria:
        errors.append(
            f"{label} has no acceptance criteria: put `ac {feature.id}.AC1: <criterion>` for each"
        )
    return errors


def _trace_findings(feature: Feature) -> list[aspect_lines.Finding]:
    """Return what is wrong with a traced feature's criterion ids and verifications."""
    found = [
        aspect_lines.Finding(
            f"`{line}` in feature `{feature.id}` is outside its acceptance criteria "
            "list: keep every criterion a bullet under `Acceptance criteria:`, with "
            "its `Verification:` line and any wrapped line indented under it",
            kind=Kind.UNLISTED_CRITERION,
        )
        for line in feature.stranded
    ]
    errors: list[str] = []
    for number, item in enumerate(feature.traced, start=1):
        if not item.id:
            errors.append(
                f"acceptance criterion {number} of feature `{feature.id}` has no id: "
                f"start its bullet with `{feature.id}.AC{number}:`"
            )
        elif not item.id.startswith(f"{feature.id}."):
            errors.append(
                f"acceptance criterion `{item.id}` is under feature `{feature.id}`: "
                f"number it `{feature.id}.AC<n>`"
            )
        label = f"`{item.id}`" if item.id else f"{number} of feature `{feature.id}`"
        if not item.text.strip():
            errors.append(
                f"acceptance criterion {label} has no text: write the observable "
                "criterion after its id"
            )
        if item.verification.strip(_VALUE_EDGES).casefold() in _NO_VERIFICATION:
            errors.append(
                f"acceptance criterion {label} has no `Verification:` line: under it, "
                "name the automated test that will prove it"
            )
    return [*found, *(aspect_lines.Finding(error) for error in errors)]


def _structure_findings(parsed: FeatureDefinition) -> list[aspect_lines.Finding]:
    """Return what is wrong with the sections of a definition, before its features."""
    found = [
        aspect_lines.Finding(f"the `## {name}` section is missing")
        for name in SECTIONS
        if name not in parsed.sections
    ]
    found += [
        aspect_lines.Finding(
            f"the `## {name}` section appears more than once: write each section "
            "once, with everything it holds under it"
        )
        for name in parsed.repeated
    ]
    if OBJECTIVE in parsed.sections and not parsed.sections[OBJECTIVE].strip():
        found.append(
            aspect_lines.Finding(
                "the objective is empty: put `objective: <what the change achieves and why>`"
            )
        )
    if FEATURES in parsed.sections and not parsed.features:
        found.append(
            aspect_lines.Finding(
                "the definition has no feature: put `feature F1: <name>`, "
                "`feature F2: <name>`, … for each"
            )
        )
    found += [
        aspect_lines.Finding(
            f"`{heading.strip()}` under `## {FEATURES}` is not a `### F<n> <name>` feature"
        )
        for heading in parsed.stray
    ]
    return found


def _feature_findings(
    feature: Feature, traced: bool, anchored: bool, source: str, aspects: bool
) -> list[aspect_lines.Finding]:
    """Return what is wrong with one feature, at the depth of checking asked for."""
    found = [aspect_lines.Finding(error) for error in _feature_errors(feature)]
    if traced:
        found += _trace_findings(feature)
    if anchored:
        found += anchor_findings(feature, source)
    if aspects:
        found += aspect_findings(feature)
    return found


def _criterion_id_findings(parsed: FeatureDefinition) -> list[aspect_lines.Finding]:
    """Return a finding for each acceptance criterion id used more than once."""
    ids = [item.id for item in parsed.acceptance() if item.id]
    return [
        aspect_lines.Finding(f"acceptance criterion id `{ident}` is used more than once")
        for ident in sorted({ident for ident in ids if ids.count(ident) > 1})
    ]


def findings(
    text: str,
    traced: bool = False,
    anchored: bool = False,
    source: str = "",
    aspects: bool = False,
) -> list[aspect_lines.Finding]:
    """Return what is wrong with a feature definition, each finding blocking or advisory.

    A blocking finding is a fault in the definition's structure, identity, quotes
    or the shape of its aspects; an advisory one is about the quality of its
    aspects and examples (``aspects.Finding``). ``check`` is the same
    list as text.
    """
    if not text.strip():
        return [aspect_lines.Finding(f"`{FILE}` is missing or empty: write it in the shape given")]
    traced = traced or anchored
    aspects = aspects if anchored else False
    parsed = parse(text, traced, anchored, aspects)
    found = _structure_findings(parsed)
    seen: set[str] = set()
    for feature in parsed.features:
        if feature.id in seen:
            found.append(aspect_lines.Finding(f"feature id `{feature.id}` is used more than once"))
        seen.add(feature.id)
        found += _feature_findings(feature, traced, anchored, source, aspects)
    return [*found, *_criterion_id_findings(parsed)]


def check(
    text: str,
    traced: bool = False,
    anchored: bool = False,
    source: str = "",
    aspects: bool = False,
) -> list[str]:
    """Return what is wrong with a feature definition (``[]`` when it is usable).

    ``traced`` also requires every acceptance criterion's id and
    ``Verification:`` line. ``anchored`` is traced and also checks each
    criterion's quotes, which must be the words of ``source`` (the issue and any
    earlier round's answers). ``aspects`` (needs ``anchored``) also checks each
    criterion's ``Aspect:``, ``Example:`` and ``Kind:`` lines
    (``aspects.criterion_findings``).
    """
    return [found.text for found in findings(text, traced, anchored, source, aspects)]


# Anchored: the issue's own words -------------------------------------------------


def _criterion_label(feature: Feature, number: int, item: Criterion) -> str:
    return f"`{item.id}`" if item.id else f"{number} of feature `{feature.id}`"


def _source_findings(label: str, item: Criterion, source: str) -> list[aspect_lines.Finding]:
    if not item.sources:
        return [
            aspect_lines.Finding(
                f"acceptance criterion {label} has no `Source:` line: under it, quote "
                'the issue\'s own words it derives from, exactly, as `Source: "…"`',
                kind=Kind.QUOTE_MISSING,
            )
        ]
    errors = []
    for quote in item.sources:
        problem = evidence.verbatim_problem(quote, source, SOURCE_MIN_WORDS)
        if problem:
            errors.append(
                aspect_lines.Finding(
                    f"the `Source:` quote `{quote}` of acceptance criterion {label} "
                    f"{problem}: copy the issue's own words exactly, one contiguous "
                    "passage per `Source:` line; a paraphrase is never the issue's words",
                    kind=Kind.QUOTE_VERBATIM,
                )
            )
    return errors


def anchor_findings(feature: Feature, source: str) -> list[aspect_lines.Finding]:
    """Return what is wrong with the ``Source:`` quotes of a feature's criteria."""
    found: list[aspect_lines.Finding] = []
    for number, item in enumerate(feature.traced, start=1):
        label = _criterion_label(feature, number, item)
        found += _source_findings(label, item, source)
    return found


def aspect_findings(feature: Feature) -> list[aspect_lines.Finding]:
    """Return what is wrong with a feature's aspects, examples and kinds."""
    found: list[aspect_lines.Finding] = []
    for number, item in enumerate(feature.traced, start=1):
        label = _criterion_label(feature, number, item)
        found += aspect_lines.criterion_findings(item, label)
    return found
