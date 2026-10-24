"""Ledger integrity: schema, ids, links, status transitions, date order and the generated index."""

from __future__ import annotations

from pathlib import Path

from raidar.ledger import index
from raidar.ledger.records import (
    FAULT_INITIAL,
    FAULT_TRANSITIONS,
    REMEDY_INITIAL,
    REMEDY_TRANSITIONS,
    FaultRecord,
    RemedyRecord,
    parse_record,
)

FAULTS_DIR = "faults"
REMEDIES_DIR = "remedies"


def _history_problems(
    name: str,
    record: FaultRecord | RemedyRecord,
    initial: str,
    transitions: dict[str, frozenset[str]],
) -> list[str]:
    problems: list[str] = []
    history = record.status_history
    if history[0].status != initial:
        problems.append(
            f"{name}: first history status must be `{initial}`, got `{history[0].status}`"
        )
    for prev, cur in zip(history, history[1:], strict=False):
        if cur.status not in transitions.get(prev.status, frozenset()):
            problems.append(f"{name}: illegal transition `{prev.status}` -> `{cur.status}`")
        if cur.date < prev.date:
            problems.append(f"{name}: history date {cur.date} precedes {prev.date}")
    problems += [
        f"{name}: unknown history status `{e.status}`"
        for e in history
        if e.status not in transitions
    ]
    if history[-1].status != record.status:
        problems.append(
            f"{name}: status `{record.status}` differs from last history status "
            f"`{history[-1].status}`"
        )
    return problems


def _load_dir(
    root: Path, sub: str, prefix: str, model: type[FaultRecord] | type[RemedyRecord]
) -> tuple[dict[str, FaultRecord | RemedyRecord], list[str]]:
    records: dict[str, FaultRecord | RemedyRecord] = {}
    problems: list[str] = []
    directory = root / sub
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        name = f"{sub}/{path.name}"
        if path.suffix != ".md" or not path.stem.startswith(f"{prefix}-"):
            problems.append(f"{name}: unexpected file (records are `{prefix}-NNN.md`)")
            continue
        try:
            record = parse_record(path, model)
        except ValueError as exc:
            problems.append(f"{name}: {exc}")
            continue
        if record.id != path.stem:
            problems.append(f"{name}: id `{record.id}` does not match the filename")
            continue
        records[record.id] = record
    return records, problems


def load_ledger(
    root: Path,
) -> tuple[dict[str, FaultRecord], dict[str, RemedyRecord], list[str]]:
    faults, fault_problems = _load_dir(root, FAULTS_DIR, "FCR", FaultRecord)
    remedies, remedy_problems = _load_dir(root, REMEDIES_DIR, "M", RemedyRecord)
    return (
        {k: v for k, v in faults.items() if isinstance(v, FaultRecord)},
        {k: v for k, v in remedies.items() if isinstance(v, RemedyRecord)},
        fault_problems + remedy_problems,
    )


def check_ledger(root: Path) -> list[str]:
    """Return every integrity problem in the ledger under ``root`` (empty when clean)."""
    faults, remedies, problems = load_ledger(root)
    for fault in faults.values():
        problems += _history_problems(fault.id, fault, FAULT_INITIAL, FAULT_TRANSITIONS)
    for remedy in remedies.values():
        problems += _history_problems(remedy.id, remedy, REMEDY_INITIAL, REMEDY_TRANSITIONS)
        problems += [
            f"{remedy.id}: fault `{fid}` does not exist"
            for fid in remedy.faults
            if fid not in faults
        ]
    problems += _readme_problems(root, faults, remedies)
    return problems


def _readme_problems(
    root: Path, faults: dict[str, FaultRecord], remedies: dict[str, RemedyRecord]
) -> list[str]:
    readme = root / index.README_NAME
    if not readme.is_file():
        return [f"{index.README_NAME}: missing; run `make ledger-index`"]
    if readme.read_text(encoding="utf-8") != index.render_index(faults, remedies):
        return [f"{index.README_NAME}: out of date; run `make ledger-index`"]
    return []
