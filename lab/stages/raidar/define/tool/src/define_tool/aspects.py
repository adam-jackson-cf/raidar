"""Aspects, examples and kinds: the lines under a criterion that name what to test.

Read with ``featuredef.parse(..., aspects=True)`` and checked by
``featuredef.check(..., aspects=True)`` (define-build-verify). Under each
acceptance criterion the intake writes::

    - F3.AC2: A missing file or a repeated name fails with exit code 1.
      Source: "Exit with code 1 and print an error message to stderr if: ..."
      Aspect: open names a file that does not exist -> the command exits with code `1`
      Aspect: open names a file that does not exist -> stderr contains `Error:`
      Example: open `missing.txt` -> exit `1`; stderr contains `Error: missing.txt does not exist`
      Verification: ...

``Aspect: <situation> -> <outcome>``
    One situation a test sets up and one outcome it asserts, numbered in order
    per criterion (``F3.AC2.A1``, ``A2``, ...).
``Example: <input> -> <expected>``
    A concrete case whose expected value, in backticks, a test asserts
    literally.
``Kind: regression``
    The criterion only says that earlier behaviour still works: it has neither
    aspects nor examples. The kind is the intake's claim, not proof: the
    engine closes it only on a mapped test that passed on the base and that
    the gate's own base-tests rule shows still passes.

``criterion_findings`` blocks (one repair turn) on: a criterion with no
``Aspect:`` (unless ``Kind: regression``); an ``Aspect:`` or ``Example:`` line
with not exactly one ASCII ``->``, an empty side or unbalanced backticks; an
``Example:`` whose expected side holds no backticked literal; a regression
criterion that carries aspects or examples. It only advises (listed in the
same repair turn) on a backticked code literal the criterion or its ``Source:``
quotes state that no aspect or example carries.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple, Protocol

from . import issuetext as issue_texts
from .kinds import Kind


def _split(text: str) -> list[str]:
    return text.split()


ARROW = "->"
REGRESSION = "regression"
KINDS = (REGRESSION,)
ASPECT = "aspect"
EXAMPLE = "example"
KIND = "kind"
_OTHER_ARROWS = re.compile(r"→|⟶|=>")
# A number, with its sign: a `-` right after a word character or a dot is a
# separator (`2024-01-05`), not a sign.
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_PLACEHOLDER = re.compile(r"<[^<>`]*>")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}


class Finding(NamedTuple):
    """One thing wrong with a criterion's lines, and whether it only advises.

    A blocking finding makes the lines unusable downstream (no aspect, an aspect
    the judge cannot split, a kind that contradicts the lines); an advisory one is
    about their quality (a long side, a value no line states). ``kind`` says what
    is wrong (``kinds.Kind``); the evaluator counts by it, never by ``text``.
    """

    text: str
    advisory: bool = False
    kind: str = Kind.STRUCTURE


# The bare type names a criterion's backticks may name: not values a test pins.
TYPE_NAMES = frozenset(
    _split(
        "bool boolean int integer float double number str string date datetime "
        "time list array dict map object null none"
    )
)


class Subject(Protocol):
    """What the checks read of a criterion (``featuredef.Criterion``)."""

    @property
    def id(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def sources(self) -> tuple[str, ...]: ...

    @property
    def kind(self) -> str: ...

    @property
    def aspects(self) -> tuple[Aspect, ...]: ...

    @property
    def examples(self) -> tuple[Example, ...]: ...


@dataclass(frozen=True)
class Aspect:
    """One ``Aspect:`` line: ``id`` (``F3.AC2.A1``), its text and its two sides.

    ``situation`` and ``outcome`` are ``""`` when the line is not one
    ``<situation> -> <outcome>`` (``arrow_problem`` says why).
    """

    id: str
    text: str
    situation: str = ""
    outcome: str = ""

    def record(self) -> dict[str, str]:
        return {"id": self.id, "situation": self.situation, "outcome": self.outcome}


@dataclass(frozen=True)
class Example:
    """One ``Example:`` line: its sides and the expected side's literals.

    ``literals`` are the backticked values of ``expected`` as a test is searched
    for them (``literal_value``): quote marks and spacing set aside, numbers in
    canonical form.
    """

    text: str
    given: str = ""
    expected: str = ""
    literals: tuple[str, ...] = ()

    def record(self) -> dict[str, object]:
        return {
            "given": self.given,
            "expected": self.expected,
            "literals": list(self.literals),
        }


# Reading ---------------------------------------------------------------------------


def _backticks_blank(text: str) -> str:
    return issue_texts.blank_code(text)


def split_arrow(text: str) -> tuple[str, str] | None:
    """Return ``(left, right)`` of a line with exactly one ASCII ``->`` outside backticks."""
    blanked = _backticks_blank(text)
    if blanked.count(ARROW) != 1:
        return None
    at = blanked.index(ARROW)
    return text[:at].strip(), text[at + len(ARROW) :].strip()


def literal_value(span: str) -> str:
    """Return a literal as a test is searched for it.

    One pair of quote marks and surrounding spacing are set aside, inner spacing
    is collapsed, and a number takes its canonical form (``2.50`` is ``2.5``).
    """
    said = " ".join(span.split())
    close = _QUOTE_PAIRS.get(said[:1])
    if close is not None and len(said) >= 2 and said.endswith(close):
        said = said[1:-1].strip()
    return _canonical(said)


def _canonical(said: str) -> str:
    if not _NUMBER.fullmatch(said):
        return said
    number = Decimal(said)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def appears(literal: str, text: str) -> bool:
    """Whether ``literal`` (as ``literal_value`` gives it) is in ``text``.

    Spacing is collapsed on both sides and an escaped quote in the text counts
    as the quote. A number is a whole number in the text, in any spelling that
    reads the same (``2.50`` for ``2.5``; ``10`` is not ``1``); anything else
    is found as a substring.
    """
    said = " ".join(text.split())
    if _NUMBER.fullmatch(literal):
        return any(_canonical(number) == literal for number in _NUMBER.findall(said))
    unescaped = said.replace('\\"', '"').replace("\\'", "'")
    return literal in said or literal in unescaped


def literals(text: str) -> tuple[str, ...]:
    """Return the backticked literals of ``text``, normalised (``literal_value``)."""
    return tuple(literal_value(span) for span in issue_texts.code_spans(text))


def read_aspects(criterion: str, lines: Sequence[str]) -> tuple[Aspect, ...]:
    """Return the ``Aspect:`` lines of a criterion, numbered in order."""
    found = []
    for number, text in enumerate(lines, start=1):
        sides = split_arrow(text)
        ident = f"{criterion}.A{number}" if criterion else f"A{number}"
        found.append(Aspect(ident, text, *sides) if sides else Aspect(ident, text))
    return tuple(found)


def read_example(text: str) -> Example:
    """Return an ``Example:`` line, with the expected side's literals parsed."""
    sides = split_arrow(text)
    if sides is None:
        return Example(text)
    return Example(text, sides[0], sides[1], literals(sides[1]))


def read_kind(said: str) -> str:
    """Return a ``Kind:`` value as compared: emphasis, code marks, case and a full stop set
    aside."""
    return said.strip().strip("*_` ").rstrip(".").strip("*_` ").casefold()


# Checking --------------------------------------------------------------------------


def _short(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def arrow_problem(text: str) -> str:
    """Return what is wrong with the arrow of an ``Aspect:`` or ``Example:`` line, or ``""``."""
    blanked = _backticks_blank(text)
    if blanked.count("`") % 2:
        return "has an unclosed backtick: close every literal's backticks"
    count = blanked.count(ARROW)
    if count == 0:
        other = _OTHER_ARROWS.search(blanked)
        return (
            f"uses `{other.group()}`: write the ASCII `->`"
            if other
            else "has no `->` between its two sides"
        )
    if count > 1:
        return (
            f"has {count} `->`: write exactly one ASCII `->`; put a literal that "
            "holds `->` in backticks"
        )
    sides = split_arrow(text)
    if sides is not None and not all(sides):
        return "has an empty side of its `->`"
    return ""


def stated_values(text: str) -> list[str]:
    """Return the code literals a text states: its backticked values, bare type names out."""
    found = [
        literal_value(span)
        for span in issue_texts.code_spans(text)
        if not _PLACEHOLDER.search(span)
    ]
    return [token for token in found if token and token.casefold() not in TYPE_NAMES]


# An Example's expected side, as the `holds no literal` hint shows it.
_EXAMPLE_HINT = '(`exit `1``, `"hello Ada"`, `[1, 2, 3]`)'


def _mentions(span: str, token: str) -> bool:
    """Whether a backticked ``span`` states ``token``: a number in any spelling, else the words.

    A number is read as ``appears`` reads it: ``2024-01-05`` states ``1`` and
    ``5``, ``09:30`` states ``9`` and ``1,000`` states ``0``, each as the issue
    wrote it.
    """
    if _NUMBER.fullmatch(token):
        return any(_canonical(number) == token for number in _NUMBER.findall(span))
    return re.search(rf"(?<!\w){re.escape(token.casefold())}(?!\w)", span.casefold()) is not None


def _literal_spans(item: Subject) -> list[str]:
    """Return the code literals the criterion's aspects and examples carry."""
    return [
        literal_value(span)
        for line in (
            *(a.text for a in item.aspects),
            *(e.text for e in item.examples),
        )
        for span in issue_texts.code_spans(line)
    ]


def _literal_findings(item: Subject, label: str) -> list[Finding]:
    """Advisory: every code literal the criterion or its quotes state is in an aspect or example."""
    spans = _literal_spans(item)
    stated = list(
        dict.fromkeys(token for text in (item.text, *item.sources) for token in stated_values(text))
    )
    missing = [token for token in stated if not any(_mentions(span, token) for span in spans)]
    return [
        Finding(
            f"the value `{token}` that acceptance criterion {label} or its quotes state "
            "is in no `aspect:` or `example:` line's backticks: write each stated "
            "value as a literal in backticks in the aspect that covers it",
            advisory=True,
            kind=Kind.VALUE_LITERAL,
        )
        for token in missing
    ]


def _example_errors(item: Subject, label: str) -> list[str]:
    errors = []
    for example in item.examples:
        where = f"the `example:` line `{_short(example.text)}` of {label}"
        if problem := arrow_problem(example.text):
            errors.append(f"{where} {problem}: write `example: <input> -> <expected>`")
        elif not example.literals:
            errors.append(
                f"the expected value of {where} holds no literal: write it in "
                f"backticks {_EXAMPLE_HINT}"
            )
    return errors


def _kind_errors(item: Subject, label: str) -> list[str]:
    errors = []
    if item.kind and item.kind not in KINDS:
        errors.append(
            f"the `kind: {item.kind}` line of acceptance criterion {label} names no "
            f"kind: the only kind is `{REGRESSION}`; leave the line out otherwise"
        )
    if item.kind == REGRESSION and (item.aspects or item.examples):
        errors.append(
            f"acceptance criterion {label} is `kind: {REGRESSION}` and so has no "
            "`aspect:` or `example:` lines: remove them, or remove the `kind:` line"
        )
    return errors


def _aspect_errors(label: str, aspect: Aspect) -> list[str]:
    if problem := arrow_problem(aspect.text):
        where = f"the `aspect:` line `{_short(aspect.text)}` ({aspect.id}) of {label}"
        return [
            f"{where} {problem}: write `aspect: <situation> -> <outcome>`, one "
            "situation a test sets up and one outcome it asserts"
        ]
    return []


def criterion_findings(item: Subject, label: str) -> list[Finding]:
    """Return what is wrong with a criterion's aspects, examples and kind, blocking or advisory."""
    errors = [*_kind_errors(item, label)]
    if item.kind == REGRESSION:
        return [Finding(error, kind=Kind.ASPECT_SHAPE) for error in errors]
    if not item.aspects:
        errors.append(
            f"acceptance criterion {label} has no `aspect:` line: under it, write "
            "one `aspect: <situation> -> <outcome>` line per situation and outcome "
            "a test must cover, or `kind: regression` when it only says earlier "
            "behaviour still works"
        )
    for aspect in item.aspects:
        errors += _aspect_errors(label, aspect)
    errors += _example_errors(item, label)
    return [
        *(Finding(error, kind=Kind.ASPECT_SHAPE) for error in errors),
        *_literal_findings(item, label),
    ]
