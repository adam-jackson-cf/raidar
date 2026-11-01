"""Coverage inputs from retained primitive records, never package executable code.

``outputs/1`` supplies a digest-checked ``criteria/1`` document. Its producer's captured
``setup/1`` UNITS token supplies the citable task units (``U12*: required``, ``U13: optional``).
``acceptance-reply/1`` supplies the final test map and retained test-source directory.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raidar_contract.criteria import validate_criteria

WORKFLOW_RECORD = "workflow/workflow.json"
_UNIT = re.compile(r"^([UC][0-9]+)(\*)?: (.*)$")


def normalised(text: str) -> str:
    """Join whitespace as the package's numbered-unit presentation does."""
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
        """What Jev sees, without source quotes or verification instructions."""
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
    """The producer's citable units and, when sealed, portable criteria."""

    ending: str | None
    units: tuple[Unit, ...]
    criteria: tuple[Criterion, ...] | None


@dataclass(frozen=True)
class NotDefined:
    """The workflow declares no criteria output."""

    reason: str


@dataclass(frozen=True)
class Unreadable:
    """Required retained evidence is missing, malformed or changed."""

    reason: str


@dataclass(frozen=True)
class Built:
    ac_tests: dict[str, tuple[str, ...]]
    sources: Path


def _inside(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    named = Path(relative)
    if named.is_absolute() or ".." in named.parts:
        return None
    path = root / named
    if any(
        part.is_symlink()
        for part in (path, *path.parents)
        if part != root and part.is_relative_to(root)
    ):
        return None
    return path if path.resolve().is_relative_to(root.resolve()) else None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def workflow_record(trace_dir: Path) -> dict[str, Any] | None:
    loaded = _json(trace_dir / WORKFLOW_RECORD)
    return loaded if isinstance(loaded, dict) else None


def _setup_stdout(record: dict[str, Any], producer: str) -> str | None:
    setups = record.get("setup/1")
    setup = setups.get(producer) if isinstance(setups, dict) else None
    call = setup.get("call") if isinstance(setup, dict) else None
    if not isinstance(call, dict):
        return None
    stdout = call.get("stdout")
    if not isinstance(stdout, str):
        return None
    return (
        stdout if hashlib.sha256(stdout.encode()).hexdigest() == call.get("stdout_sha256") else None
    )


def _units(record: dict[str, Any], producer: str) -> tuple[Unit, ...] | None:
    stdout = _setup_stdout(record, producer)
    if stdout is None:
        return None
    try:
        document = json.loads(stdout)
        text = document["tokens"]["UNITS"]
    except (ValueError, KeyError, TypeError):
        return None
    return _numbered_units(text) if isinstance(text, str) else None


def _numbered_units(text: str) -> tuple[Unit, ...] | None:
    units: list[Unit] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _UNIT.fullmatch(line)
        if match is None or match[1] in seen:
            return None
        seen.add(match[1])
        units.append(Unit(match[1], match[3], match[2] == "*"))
    return tuple(units)


def _criteria(trace_dir: Path, output: dict[str, Any]) -> tuple[Criterion, ...] | Unreadable:
    path = _inside(trace_dir, output.get("host_path"))
    if path is None:
        return Unreadable("the criteria output has no safe retained path")
    try:
        data = path.read_bytes()
        if len(data) != output.get("bytes") or hashlib.sha256(data).hexdigest() != output.get(
            "sha256"
        ):
            return Unreadable("the retained criteria output differs from its captured digest")
        document = validate_criteria(json.loads(data))
    except (OSError, ValueError) as error:
        return Unreadable(f"the retained criteria output is unreadable: {error}")
    return tuple(
        Criterion(
            item.id,
            item.criterion,
            item.sources,
            tuple(f"{probe.situation} → {probe.outcome}" for probe in item.aspects),
            tuple(f"{probe.situation} → {probe.outcome}" for probe in item.examples),
            item.kind == "regression",
        )
        for item in document.all_criteria()
    )


def _criteria_outputs(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]] | None:
    outputs = record.get("outputs/1")
    if not isinstance(outputs, dict):
        return None
    found: list[tuple[str, dict[str, Any]]] = []
    for producer, named in outputs.items():
        if not isinstance(named, dict):
            return None
        found.extend(
            (producer, output)
            for output in named.values()
            if isinstance(output, dict) and output.get("contract") == "criteria/1"
        )
    return found


def read_define(trace_dir: Path) -> Defined | NotDefined | Unreadable:
    """Read the single criteria producer; ambiguous or changed evidence fails closed."""
    record = workflow_record(trace_dir)
    if record is None:
        return Unreadable("no workflow record: criteria production cannot be known")
    candidates = _criteria_outputs(record)
    if candidates is None:
        return Unreadable("the workflow holds no valid outputs/1 record")
    if not candidates:
        return NotDefined("the workflow has no criteria/1 output")
    if len(candidates) != 1:
        return Unreadable("coverage requires one unambiguous criteria/1 producer")
    return _defined(trace_dir, record, *candidates[0])


def _defined(
    trace_dir: Path, record: dict[str, Any], producer: str, output: dict[str, Any]
) -> Defined | Unreadable:
    units = _units(record, producer)
    if units is None:
        return Unreadable("the criteria producer's setup/1 units are missing or malformed")
    endings = record.get("endings", {})
    ending = endings.get(producer) if isinstance(endings, dict) else None
    if output.get("error") is not None:
        return Defined(ending, units, None)
    criteria = _criteria(trace_dir, output)
    return criteria if isinstance(criteria, Unreadable) else Defined(ending, units, criteria)


def read_build(trace_dir: Path) -> Built | Unreadable:
    """The acceptance-reply primitive's final test map and retained sources."""
    record = workflow_record(trace_dir)
    reply = record.get("acceptance-reply/1") if record else None
    if not isinstance(reply, dict):
        return Unreadable("the workflow holds no acceptance-reply/1 record")
    mapped = reply.get("ac_tests")
    if not isinstance(mapped, dict):
        return Unreadable("acceptance-reply/1 holds no ac_tests map")
    sources = _inside(trace_dir, reply.get("sources"))
    if sources is None or not sources.is_dir():
        return Unreadable("the final test sources were not retained")
    tests: dict[str, tuple[str, ...]] = {}
    for criterion, names in mapped.items():
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            return Unreadable(f"ac_tests of {criterion} is not a list of test ids")
        tests[str(criterion)] = tuple(dict.fromkeys(names))
    return Built(tests, sources)
