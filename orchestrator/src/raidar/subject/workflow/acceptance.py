"""The acceptance criteria of a checkpoint and the tests the builder maps to them.

One ``Acceptance`` is shared by the stages of a checkpoint: the define stage loads it from the
sealed feature definition, the build stage merges the builder's ``ac_tests`` into it and checks
the mapping, the verify stage judges it. The mapping rules are factory-x-loops'
``capability/define/audit.py``
at ``ac5db0f``: a turn lists the criteria whose tests it set or changed (those take the turn's
tests, the rest keep theirs); every criterion but a ``Kind: regression`` one needs a mapped test;
each mapped test's file must be in the working tree.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from raidar.subject.workflow.define_tool import featuredef
from raidar.subject.workflow.define_tool.aspects import REGRESSION


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
    """The criteria a sealed feature definition holds and the builder's merged mapping."""

    features: tuple[featuredef.Feature, ...] = ()
    ac_tests: dict[str, list[str]] = field(default_factory=dict)
    sources: str | None = None
    """Where the host kept the final judged pass's test sources (relative to the agent logs)."""

    def load(self, definition: str) -> None:
        """Read the criteria from the rendered feature definition."""
        self.features = featuredef.parse(
            definition, traced=True, anchored=True, aspects=True
        ).features

    @property
    def criteria(self) -> tuple[featuredef.Criterion, ...]:
        return tuple(item for feature in self.features for item in feature.traced)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.criteria]

    @property
    def optional(self) -> list[str]:
        """The criteria that may go unmapped: ``Kind: regression`` ones (earlier tests)."""
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
