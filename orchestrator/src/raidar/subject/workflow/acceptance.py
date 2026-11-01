"""The acceptance criteria of a checkpoint and the tests the builder maps to them.

One ``Acceptance`` is shared by the checkpoint's ``acceptance-reply/1`` and ``jev-judge/1``
runtimes. It is loaded from a package's ``criteria/1`` output (``raidar_contract.criteria``, never
a package's own code), the builder's ``ac_tests`` are merged into it and the mapping is checked,
and the judge decides each criterion from it. The mapping rules are factory-x-loops'
``capability/define/audit.py`` at ``ac5db0f``: a turn lists the criteria whose tests it set or
changed (those take the turn's tests, the rest keep theirs); every criterion but a regression
one needs a mapped test; each mapped test's file must be in the working tree.

The criterion types here are the judge's reading of ``criteria/1``: an aspect is
``<criterion id>.A<n>`` in document order, an example's literals are the backticked values of its
expected side as a test is searched for them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from raidar_contract.criteria import Criteria

REGRESSION = "regression"
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
# A markdown code span (CommonMark): a run of backticks, the text, and a closing run of exactly
# as many. A span that holds a backtick is fenced by a longer run.
_CODE_SPAN = re.compile(r"(?<!`)(?P<fence>`+)(?!`)(?P<body>[^\n]+?)(?<!`)(?P=fence)(?!`)")


def _span_body(body: str) -> str:
    """A code span's content, without the one space of padding on each side."""
    if len(body) > 2 and body[0] == body[-1] == " " and body.strip():
        return body[1:-1]
    return body


def _canonical(said: str) -> str:
    if not _NUMBER.fullmatch(said):
        return said
    number = Decimal(said)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def literal_value(span: str) -> str:
    """A literal as a test is searched for it.

    One pair of quote marks and surrounding spacing are set aside, inner spacing is collapsed,
    and a number takes its canonical form (``2.50`` is ``2.5``).
    """
    said = " ".join(span.split())
    close = _QUOTE_PAIRS.get(said[:1])
    if close is not None and len(said) >= 2 and said.endswith(close):
        said = said[1:-1].strip()
    return _canonical(said)


def literals(text: str) -> tuple[str, ...]:
    """The backticked literals of ``text``, normalised (``literal_value``)."""
    return tuple(
        literal_value(_span_body(found.group("body"))) for found in _CODE_SPAN.finditer(text)
    )


def appears(literal: str, text: str) -> bool:
    """Whether ``literal`` (as ``literal_value`` gives it) is in ``text``.

    Spacing is collapsed on both sides and an escaped quote in the text counts as the quote. A
    number is a whole number in the text, in any spelling that reads the same (``2.50`` for
    ``2.5``; ``10`` is not ``1``); anything else is found as a substring.
    """
    said = " ".join(text.split())
    if _NUMBER.fullmatch(literal):
        return any(_canonical(number) == literal for number in _NUMBER.findall(said))
    unescaped = said.replace('\\"', '"').replace("\\'", "'")
    return literal in said or literal in unescaped


@dataclass(frozen=True)
class Aspect:
    """One aspect: ``id`` (``F3.AC2.A1``) and the situation and outcome a test must show."""

    id: str
    situation: str
    outcome: str

    @property
    def text(self) -> str:
        return f"{self.situation} -> {self.outcome}"


@dataclass(frozen=True)
class Example:
    """One example (its input and expected sides) and the expected side's literals."""

    given: str
    expected: str
    literals: tuple[str, ...] = ()


@dataclass(frozen=True)
class Criterion:
    """One acceptance criterion as the judge reads it (``kind`` is ``""`` or ``regression``)."""

    id: str
    text: str
    sources: tuple[str, ...] = ()
    aspects: tuple[Aspect, ...] = ()
    examples: tuple[Example, ...] = ()
    kind: str = ""


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    traced: tuple[Criterion, ...] = ()


def features_of(criteria: Criteria) -> tuple[Feature, ...]:
    """The judge's reading of a validated ``criteria/1`` document, in document order."""
    return tuple(
        Feature(
            feature.id,
            feature.name,
            tuple(
                Criterion(
                    item.id,
                    item.criterion,
                    item.sources,
                    tuple(
                        Aspect(f"{item.id}.A{number}", probe.situation, probe.outcome)
                        for number, probe in enumerate(item.aspects, start=1)
                    ),
                    tuple(
                        Example(probe.situation, probe.outcome, literals(probe.outcome))
                        for probe in item.examples
                    ),
                    REGRESSION if item.kind == "regression" else "",
                )
                for item in feature.criteria
            ),
        )
        for feature in criteria.features
    )


def _names(items: Sequence[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _strings(row: Mapping[str, Any], key: str) -> list[str]:
    value = row.get(key)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def rows(value: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """The objects of the list ``value[key]`` (anything else is dropped)."""
    found = value.get(key)
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def file_of(identifier: str) -> str:
    """The file part of a test identifier (``path::Class::test_name``)."""
    return identifier.split("::", 1)[0].strip()


@dataclass
class Acceptance:
    """The criteria a package produced and the builder's merged mapping."""

    features: tuple[Feature, ...] = ()
    ac_tests: dict[str, list[str]] = field(default_factory=dict)
    sources: str | None = None
    """Where the host kept the final judged pass's test sources (relative to the agent logs)."""

    def load(self, criteria: Criteria) -> None:
        """Read the criteria of a validated ``criteria/1`` document."""
        self.features = features_of(criteria)

    @property
    def criteria(self) -> tuple[Criterion, ...]:
        return tuple(item for feature in self.features for item in feature.traced)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.criteria]

    @property
    def optional(self) -> list[str]:
        """The criteria that may go unmapped: regression ones (earlier tests)."""
        return [item.id for item in self.criteria if item.kind == REGRESSION]

    def merge(self, reported: Sequence[Mapping[str, Any]]) -> None:
        """Lay a turn's ``ac_tests`` over the mapping: a listed known criterion takes its tests."""
        known = set(self.ids)
        for item in reported:
            ident = _text(item, "criterion").strip()
            tests = [name.strip() for name in _strings(item, "tests") if name.strip()]
            if ident in known and tests:
                self.ac_tests[ident] = tests

    def errors(self, reported: Sequence[Mapping[str, Any]], present: Collection[str]) -> list[str]:
        """What is wrong with the mapping after a turn that reported ``reported``.

        ``present`` is the set of mapped files found in the working tree.
        """
        known = self.ids
        errors = []
        unknown = sorted({_text(item, "criterion").strip() for item in reported} - {*known})
        if unknown:
            errors.append(
                f"`ac_tests` names {_names(unknown)}, which the feature definition does "
                "not have: use its criterion ids (`F1.AC1`, …)"
            )
        optional = self.optional
        unmapped = [
            ident for ident in known if not self.ac_tests.get(ident) and ident not in optional
        ]
        if unmapped:
            errors.append(
                f"`ac_tests` maps no test to {_names(unmapped)}: give every acceptance "
                "criterion the identifiers of the tests that prove it"
            )
        absent = absent_tests(self.ac_tests, lambda path: path in present)
        if absent:
            named = "; ".join(f"{ident}: {_names(tests)}" for ident, tests in absent.items())
            errors.append(
                f"`ac_tests` names tests in files the working tree does not have ({named}): "
                "write those tests, or list the criterion again with the identifiers "
                "(`path::test_name`) of tests that exist"
            )
        return errors

    @property
    def mapped_files(self) -> list[str]:
        """Every file a mapped test names, sorted."""
        return sorted({file_of(name) for tests in self.ac_tests.values() for name in tests})


def absent_tests(
    mapping: Mapping[str, Sequence[str]], exists: Callable[[str], bool]
) -> dict[str, list[str]]:
    """By criterion, each mapped test whose file ``exists`` does not find."""
    found: dict[str, list[str]] = {}
    for ident, tests in mapping.items():
        gone = [name for name in tests if not exists(file_of(name))]
        if gone:
            found[ident] = gone
    return found
