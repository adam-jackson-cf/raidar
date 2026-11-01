"""P8: check normalized native-session evidence against workspace/workflow instructions.

Harness adapters parse their own records. This check knows only loaded skill roots,
instruction text, audit completeness and versioned developer/system message digests.
Missing native evidence is unmeasured, never a passing isolation default.

Each instruction the harness merged names the file it came from, and its lines must be lines that
file could hold *before the turn*: the previous step's stored snapshot of that file and the text
the workflow placed there. No snapshot is retained from before a turn of the step's own, so the
step's final snapshot never vouches for anything: the first step has none from before it, and a
resumed turn follows turns in which the agent could have written the file, so a line the workflow
did not place there cannot be checked and is ``unmeasured``, as is an instruction whose origin the
harness did not record. A line another file could vouch for does not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from raidar.contract.snapshot import APP_SOURCE
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

WORKFLOW_FILE = "workflow/workflow.json"
AGENTS_FILES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
_SHOWN = 3


_ReadBytes = Callable[[PurePosixPath], bytes | None]
"""Bytes of a file named relative to a step's ``agent`` directory; None when unreadable."""
_ReadWorkspace = Callable[[PurePosixPath], str | None]
"""Text of a workspace file as the step began; None when the file is absent."""


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stored_snapshot(a: Audit, step: str) -> Path | None:
    """The stored ``/app`` snapshot of ``step`` on the host, or None."""
    for result in a.record.step_results:
        if result.step == step and result.snapshot is not None:
            root = a.trial_dir / result.snapshot.path
            return root if root.is_dir() else None
    return None


def _before_snapshot(a: Audit, step: str) -> Path | None:
    """The workspace as the step began: the previous step's stored snapshot (None for the first)."""
    order = [r.step for r in a.record.step_results]
    index = order.index(step) if step in order else 0
    return _stored_snapshot(a, order[index - 1]) if index > 0 else None


def _snapshot_reader(root: Path) -> _ReadWorkspace:
    def read(file: PurePosixPath) -> str | None:
        path = root / file
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    return read


def _agent_reader(agent: Path) -> _ReadBytes:
    def read(path: PurePosixPath) -> bytes | None:
        try:
            return (agent / path).read_bytes()
        except OSError:
            return None

    return read


def _text_lines(text: str | None) -> set[str]:
    """The stripped, non-empty lines of ``text`` (none for an absent file)."""
    return {line.strip() for line in (text or "").splitlines() if line.strip()}


def _relative(path: PurePosixPath) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def _component_placements(workflow: dict[str, Any]) -> list[tuple[PurePosixPath, Any]]:
    components = workflow.get("components")
    return [
        (PurePosixPath(entry["path"]), entry["record"])
        for entry in (components if isinstance(components, list) else [])
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("record"), str)
    ]


def _placements(workflow: Any) -> list[tuple[PurePosixPath, PurePosixPath]]:
    """``(workspace file, step record)`` for each text the workflow placed in the workspace.

    A component names its file and its host-written record; the package workflow writes no
    other placement. One with no recorded file vouches for no file.
    """
    if not isinstance(workflow, dict):
        return []
    placed = _component_placements(workflow)
    return [
        (path, PurePosixPath(record))
        for path, record in placed
        if isinstance(record, str) and _relative(path) and _relative(PurePosixPath(record))
    ]


def _placed_lines(workflow: Any, read: _ReadBytes) -> dict[PurePosixPath, set[str]]:
    """The lines each workspace file was given by the workflow's components."""
    placed: dict[PurePosixPath, set[str]] = {}
    for file, record in _placements(workflow):
        data = read(record)
        text = None if data is None else data.decode("utf-8", errors="replace")
        placed.setdefault(file, set()).update(_text_lines(text))
    return placed


@dataclass(frozen=True)
class _PreTurn:
    """What the workspace's instruction files could hold before one turn."""

    placed: dict[PurePosixPath, set[str]] = field(default_factory=dict)
    before: _ReadWorkspace | None = None
    """The step's starting workspace; None for the first step (nothing was stored before it)."""
    resumed: bool = False
    """A later turn of the step: no snapshot was retained from before it, and the agent may have
    written instruction files earlier in the step, so unplaced text there cannot be checked."""

    def lines(self, file: PurePosixPath) -> set[str]:
        found = set(self.placed.get(file, ()))
        if self.before is not None:
            found |= _text_lines(self.before(file))
        return found


def _broken_components(workflow: Any, read: _ReadBytes) -> list[tuple[PurePosixPath, str]]:
    """The recorded components whose record is missing or does not hold its digest, or whose
    path leaves the workspace."""
    entries = workflow.get("components") if isinstance(workflow, dict) else None
    paths = [
        (PurePosixPath(entry["path"]), entry)
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]
    faults = [(path, _component_fault(entry, path, read)) for path, entry in paths]
    return [(path, reason) for path, reason in faults if reason is not None]


def _component_fault(entry: dict[str, object], path: PurePosixPath, read: _ReadBytes) -> str | None:
    """Why one recorded component is broken, or None."""
    record = entry.get("record")
    text = read(PurePosixPath(record)) if isinstance(record, str) else None
    if text is None:
        return "its record is missing"
    if hashlib.sha256(text).hexdigest() != entry.get("sha256"):
        return "its record is not the recorded digest"
    if not _relative(path):
        return "it is placed outside the workspace"
    return None


def _origin_files(origin: str) -> list[PurePosixPath] | None:
    """The workspace files an instruction from ``origin`` can be made of; None if outside ``/app``.

    A harness names either the file (claude) or the folder whose files it merged, with the folders
    above it up to the workspace root (codex).
    """
    path = PurePosixPath(origin)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(APP_SOURCE):
        return None
    relative = path.relative_to(APP_SOURCE)
    if relative.name in AGENTS_FILES:
        return [relative]
    return [folder / name for folder in (relative, *relative.parents) for name in AGENTS_FILES]


def _instruction_findings(
    instruction: dict[str, Any], pre: _PreTurn
) -> tuple[list[str], list[str]]:
    origin = instruction.get("path")
    if not isinstance(origin, str) or not origin:
        return [], ["an instruction's origin file is not recorded"]
    files = _origin_files(origin)
    if files is None:
        return [f"instructions from outside the workspace: {origin}"], []
    allowed = set().union(*(pre.lines(file) for file in files))
    foreign = [
        line.strip()
        for line in str(instruction.get("text", "")).splitlines()
        if line.strip() and line.strip() not in allowed
    ]
    return _foreign_findings(origin, foreign, pre) if foreign else ([], [])


def _foreign_findings(
    origin: str, foreign: list[str], pre: _PreTurn
) -> tuple[list[str], list[str]]:
    if pre.before is None:
        return [], [f"no workspace snapshot from before the first step to check {origin} against"]
    if pre.resumed:
        return [], [f"no workspace snapshot from before this resumed turn to check {origin}"]
    return [
        f"instructions from outside {origin}: {foreign[0][:60]!r} "
        f"and {len(foreign) - 1} more line(s)"
    ], []


def _audit_findings(
    audit: dict[str, Any], pre: _PreTurn, read_source: _ReadBytes, log_dir: PurePosixPath
) -> tuple[list[str], list[str]]:
    bad, missing = _source_findings(audit.get("sources") or [], read_source, log_dir)
    bad += list(audit.get("violations") or [])
    missing += list(audit.get("unmeasured") or [])
    if not audit.get("records"):
        missing.append("no native session records")
    bad.extend(
        f"skill root outside the workspace: {root}"
        for root in audit.get("skill_roots") or []
        if not PurePosixPath(root).is_relative_to(APP_SOURCE)
    )
    for instruction in audit.get("instructions") or []:
        found, absent = _instruction_findings(instruction, pre)
        bad += found
        missing += absent
    return bad, missing


def _source_findings(
    sources: list[dict[str, str]], read: _ReadBytes, log_dir: PurePosixPath
) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    missing: list[str] = []
    if not sources:
        missing.append("no native audit source files")
    for source in sources:
        relative = PurePosixPath(source["path"])
        if not _relative(relative):
            bad.append(f"audit source outside the turn: {relative}")
            continue
        data = read(log_dir / relative)
        if data is None:
            missing.append(f"native audit source missing: {relative}")
            continue
        if hashlib.sha256(data).hexdigest() != source.get("sha256"):
            bad.append(f"native audit source changed: {relative}")
    return bad, missing


def _turn_findings(
    turn: dict[str, Any], pre: _PreTurn, read: _ReadBytes
) -> tuple[int, list[str], list[str]]:
    audit = turn.get("session_audit")
    if not isinstance(audit, dict):
        return 0, [], ["no native session audit"]
    log_dir = turn.get("log_dir")
    relative = PurePosixPath(log_dir) if isinstance(log_dir, str) else None
    if relative is None or not _relative(relative):
        return 0, [], ["no valid turn log directory"]
    bad, missing = _audit_findings(audit, pre, read, relative)
    return int(audit.get("records") or 0), bad, missing


def _record_messages(audit: Any, messages: dict[str, list[dict[str, str]]]) -> None:
    if not isinstance(audit, dict):
        return
    version = f"{audit.get('harness')}@{audit.get('version')}"
    group = messages.setdefault(version, [])
    for message in audit.get("messages") or []:
        if message not in group:
            group.append(message)


def _step_findings(
    step: str,
    workflow: Any,
    read: _ReadBytes,
    before: _ReadWorkspace | None,
    violations: dict[str, str],
    unmeasured: list[str],
    messages: dict[str, list[dict[str, str]]],
) -> int:
    """Fold one step's workflow record into the findings; return its checked native records."""
    turns = workflow.get("turns") if isinstance(workflow, dict) else None
    if not isinstance(turns, list) or not turns:
        unmeasured.append(f"{step}: no readable {WORKFLOW_FILE}")
        return 0
    placed = _placed_lines(workflow, read)
    for path, why in _broken_components(workflow, read):
        violations.setdefault(f"component {path}: {why}", f"component {path}: {why} ({step})")
    checked = 0
    for index, turn in enumerate(turns):
        label = f"{step}/{turn.get('stage')}/{turn.get('turn')}"
        pre = _PreTurn(placed, before, resumed=index > 0)
        records, bad, missing = _turn_findings(turn, pre, read)
        checked += records
        for finding in bad:
            violations.setdefault(finding, f"{finding} ({label})")
        unmeasured += [f"{label}: {reason}" for reason in missing]
        _record_messages(turn.get("session_audit"), messages)
    return checked


def _verdict(
    checked: int,
    violations: dict[str, str],
    unmeasured: list[str],
    messages: dict[str, list[dict[str, str]]],
) -> HealthCheck:
    unmeasured = list(dict.fromkeys(unmeasured))
    if violations:
        found = list(violations.values())
        more = f"; and {len(found) - _SHOWN} more" if len(found) > _SHOWN else ""
        detail = "; ".join(found[:_SHOWN]) + more
        return make_check(
            "P8", VIOLATION, detail, findings=found, unmeasured=unmeasured, messages=messages
        )
    if unmeasured or not checked:
        detail = "; ".join(unmeasured) or "no attempted step"
        return make_check("P8", UNMEASURED, detail, unmeasured=unmeasured, messages=messages)
    return make_check(
        "P8",
        PASS,
        f"{checked} native record(s) saw only workspace skills and instructions",
        messages=messages,
    )


def check_p8(a: Audit) -> HealthCheck:
    if a.record.agent_override:
        return make_check(
            "P8", UNMEASURED, f"agent override {a.record.agent_override!r} runs no native session"
        )
    violations: dict[str, str] = {}
    unmeasured: list[str] = []
    messages: dict[str, list[dict[str, str]]] = {}
    checked = 0
    for attempted in a.attempted:
        agent = a.trial_dir / "steps" / attempted.step / "agent"
        snapshot = _before_snapshot(a, attempted.step)
        checked += _step_findings(
            attempted.step,
            _json(agent / WORKFLOW_FILE),
            _agent_reader(agent),
            None if snapshot is None else _snapshot_reader(snapshot),
            violations,
            unmeasured,
            messages,
        )
    return _verdict(checked, violations, unmeasured, messages)
