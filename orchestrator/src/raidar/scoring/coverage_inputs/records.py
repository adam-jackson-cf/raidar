"""A step's retained define and build artifacts, read host-side for the coverage scorers.

The workflow runner keeps, per attempted step under the agent log directory,
``workflow/workflow.json`` and the files it names: the define stage's ``units.json`` and the
sealed ``feature-definition.md``, and the build's final ``ac_tests`` map with the final test
sources (``build.sources``). This module reads those files and nothing else; it never imports the
workflow's own code (scoring stays independent of workflows), so the formats are parsed here from
what is stored: units as ``[{id, text, required, citable}]``; criteria as the compact markdown
``- F1.AC1: <statement>`` with indented ``Source:``, ``Aspect:``, ``Example:`` and ``Kind:`` lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKFLOW_RECORD = "workflow/workflow.json"
REGRESSION = "regression"
_CRITERION = re.compile(r"^- (?P<id>[A-Z]+[0-9]+\.AC[0-9]+)\s*:?\s*(?P<text>.*)$")
_FIELD = re.compile(r"^\s+(Source|Aspect|Example|Kind|Verification)\s*:\s*(?P<text>.*)$")
_CONTINUATION = re.compile(r"^\s{2,}\S")
_QUOTES = '"“”'


def normalised(text: str) -> str:
    """The text on one line, runs of whitespace joined (how the definition shows a unit)."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Unit:
    id: str
    text: str
    required: bool


@dataclass(frozen=True)
class Criterion:
    id: str
    text: str
    sources: tuple[str, ...]
    aspects: tuple[str, ...]
    examples: tuple[str, ...]
    regression: bool

    def view(self) -> dict[str, Any]:
        """What Jev is shown of a criterion (its ``Source:`` and ``Verification:`` dropped)."""
        return {
            "statement": self.text,
            "aspects": list(self.aspects),
            "examples": list(self.examples),
        }

    def cites(self, unit: Unit) -> bool:
        wanted = normalised(unit.text)
        return any(normalised(source) == wanted for source in self.sources)


@dataclass(frozen=True)
class Defined:
    """A step whose workflow defined: the units, and the criteria when a definition was sealed."""

    ending: str | None
    units: tuple[Unit, ...]
    criteria: tuple[Criterion, ...] | None


@dataclass(frozen=True)
class NotDefined:
    """The step ran no define stage."""

    reason: str


@dataclass(frozen=True)
class Unreadable:
    """Evidence a measurement needs is missing or malformed."""

    reason: str


@dataclass(frozen=True)
class Built:
    """The final mapped tests: criterion id to test ids, and where their sources are kept."""

    ac_tests: dict[str, tuple[str, ...]]
    sources: Path


def _inside(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def workflow_record(trace_dir: Path) -> dict[str, Any] | None:
    loaded = _json(trace_dir / WORKFLOW_RECORD)
    return loaded if isinstance(loaded, dict) else None


def _units(trace_dir: Path, record: dict[str, Any]) -> tuple[Unit, ...] | None:
    named = _inside(trace_dir, record.get("units"))
    if named is None:
        return None
    loaded = _json(named.with_suffix(".json"))
    if not isinstance(loaded, list):
        return None
    found = []
    for item in loaded:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return None
        if not isinstance(item.get("text"), str):
            return None
        found.append(Unit(item["id"], item["text"], item.get("required") is True))
    return tuple(found)


def parse_criteria(text: str) -> tuple[Criterion, ...]:
    """The acceptance criteria of a rendered feature definition, in order."""
    found: list[dict[str, Any]] = []
    open_item: dict[str, Any] | None = None
    last: list[str] | None = None  # the field a wrapped line extends (None: the statement)
    for line in text.splitlines():
        start = _CRITERION.match(line)
        field = _FIELD.match(line)
        if start:
            open_item = {"id": start["id"], "text": start["text"].strip(), "fields": []}
            found.append(open_item)
            last = None
        elif field and open_item is not None:
            last = [field[1], field["text"].strip()]
            open_item["fields"].append(last)
        elif open_item is not None and _CONTINUATION.match(line):
            if last is None:
                open_item["text"] = f"{open_item['text']} {line.strip()}"
            else:
                last[1] += f" {line.strip()}"
        else:
            open_item = None
    return tuple(_criterion(item) for item in found)


def _criterion(item: dict[str, Any]) -> Criterion:
    fields: list[list[str]] = item["fields"]

    def values(name: str) -> tuple[str, ...]:
        return tuple(text for key, text in fields if key == name)

    return Criterion(
        id=item["id"],
        text=item["text"],
        sources=tuple(source.strip(_QUOTES) for source in values("Source")),
        aspects=values("Aspect"),
        examples=values("Example"),
        regression=REGRESSION in {kind.strip().lower() for kind in values("Kind")},
    )


def read_define(trace_dir: Path) -> Defined | NotDefined | Unreadable:
    """The step's define evidence; ``NotDefined`` when its workflow has no define stage."""
    record = workflow_record(trace_dir)
    if record is None:
        return Unreadable("no workflow record: whether the step defined cannot be known")
    if "define" not in (record.get("stages") or []):
        return NotDefined("the arm's workflow has no define stage")
    define = record.get("define")
    if not isinstance(define, dict):
        return Unreadable("the workflow record holds no define record")
    units = _units(trace_dir, define)
    if units is None:
        return Unreadable("the define stage's units were not retained or do not read")
    return _defined(trace_dir, define, units)


def _defined(
    trace_dir: Path, define: dict[str, Any], units: tuple[Unit, ...]
) -> Defined | Unreadable:
    ending = define.get("ending") if isinstance(define.get("ending"), str) else None
    written = _inside(trace_dir, define.get("definition"))
    if written is None:
        return Defined(ending, units, None)
    try:
        return Defined(ending, units, parse_criteria(written.read_text(encoding="utf-8")))
    except OSError:
        return Unreadable("the sealed feature definition is named but cannot be read")


def read_build(trace_dir: Path) -> Built | Unreadable:
    """The final ``ac_tests`` map and the directory of the retained test sources."""
    record = workflow_record(trace_dir)
    build = record.get("build") if record else None
    if not isinstance(build, dict):
        return Unreadable("the workflow record holds no build record")
    mapped = build.get("ac_tests")
    if not isinstance(mapped, dict):
        return Unreadable("the build record holds no ac_tests")
    sources = _inside(trace_dir, build.get("sources"))
    if sources is None or not sources.is_dir():
        return Unreadable("the final test sources were not retained")
    tests: dict[str, tuple[str, ...]] = {}
    for criterion, names in mapped.items():
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            return Unreadable(f"ac_tests of {criterion} is not a list of test ids")
        tests[str(criterion)] = tuple(dict.fromkeys(names))
    return Built(tests, sources)
