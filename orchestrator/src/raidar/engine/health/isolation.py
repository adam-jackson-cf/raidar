"""P8: check normalized native-session evidence against workspace/workflow instructions.

Harness adapters parse their own records. This check knows only loaded skill roots,
instruction text, audit completeness and versioned developer/system message digests.
Missing native evidence is unmeasured, never a passing isolation default.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from raidar.contract.snapshot import APP_SOURCE
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

WORKFLOW_FILE = "workflow/workflow.json"
AGENTS_FILES = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
_SHOWN = 3


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _snapshot_roots(a: Audit, step: str) -> list[Path]:
    """The stored ``/app`` snapshots of ``step`` and of the step before it, on the host."""
    order = [r.step for r in a.record.step_results]
    snapshots = {r.step: r.snapshot for r in a.record.step_results if r.snapshot is not None}
    index = order.index(step) if step in order else None
    names = [step] if index is None else order[max(index - 1, 0) : index + 1]
    roots = [a.trial_dir / snapshots[n].path for n in names if n in snapshots]
    return [root for root in roots if root.is_dir()]


def _file_lines(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {line.strip() for line in lines if line.strip()}


def _workspace_lines(a: Audit, step: str) -> set[str] | None:
    """Lines of the workspace's AGENTS files in the step's snapshot and its predecessor's, and
    of the text the step's workflow placed in them (its records)."""
    roots = _snapshot_roots(a, step)
    if not roots:
        return None
    agent = a.trial_dir / "steps" / step / "agent"
    workflow = _json(agent / WORKFLOW_FILE)
    placed = workflow.get("instructions") if isinstance(workflow, dict) else None
    records = [agent / path for path in placed or [] if isinstance(path, str)]
    files = [root / name for root in roots for name in AGENTS_FILES] + records
    return {line for path in files for line in _file_lines(path)} | _component_lines(agent)


def _broken_components(agent: Path) -> list[tuple[PurePosixPath, str]]:
    """The recorded components whose record is missing or does not hold its digest, or whose
    path leaves the workspace."""
    workflow = _json(agent / WORKFLOW_FILE)
    entries = workflow.get("components") if isinstance(workflow, dict) else None
    broken: list[tuple[PurePosixPath, str]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = PurePosixPath(entry["path"])
        reason = _component_fault(agent, entry, path)
        if reason is not None:
            broken.append((path, reason))
    return broken


def _component_fault(agent: Path, entry: dict[str, object], path: PurePosixPath) -> str | None:
    """Why one recorded component is broken, or None."""
    record = entry.get("record")
    try:
        text = (agent / record).read_bytes() if isinstance(record, str) else None
    except OSError:
        text = None
    if text is None:
        return "its record is missing"
    if hashlib.sha256(text).hexdigest() != entry.get("sha256"):
        return "its record is not the recorded digest"
    if path.is_absolute() or ".." in path.parts:
        return "it is placed outside the workspace"
    return None


def _component_lines(agent: Path) -> set[str]:
    """Lines of the recorded instruction files (``AGENTS.md`` components)."""
    workflow = _json(agent / WORKFLOW_FILE)
    entries = workflow.get("components") if isinstance(workflow, dict) else None
    lines: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        is_file = isinstance(entry, dict) and isinstance(entry.get("path"), str)
        if is_file and PurePosixPath(entry["path"]).name in AGENTS_FILES:
            record = entry.get("record")
            if isinstance(record, str):
                lines |= _file_lines(agent / record)
    return lines


def _instructions(text: str, allowed: set[str] | None) -> tuple[list[str], list[str]]:
    if allowed is None:
        return [], ["no stored /app snapshot to read workspace instructions from"]
    foreign = [
        line.strip() for line in text.splitlines() if line.strip() and line.strip() not in allowed
    ]
    if not foreign:
        return [], []
    return [
        f"instructions from outside the workspace: {foreign[0][:60]!r} "
        f"and {len(foreign) - 1} more line(s)"
    ], []


def _audit_findings(
    audit: dict[str, Any], allowed: set[str] | None, turn_dir: Path
) -> tuple[list[str], list[str]]:
    bad, missing = _source_findings(audit.get("sources") or [], turn_dir)
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
        found, absent = _instructions(instruction["text"], allowed)
        bad += found
        missing += absent
    return bad, missing


def _source_findings(sources: list[dict[str, str]], turn_dir: Path) -> tuple[list[str], list[str]]:
    bad: list[str] = []
    missing: list[str] = []
    if not sources:
        missing.append("no native audit source files")
    for source in sources:
        relative = PurePosixPath(source["path"])
        if relative.is_absolute() or ".." in relative.parts:
            bad.append(f"audit source outside the turn: {relative}")
            continue
        try:
            digest = hashlib.sha256((turn_dir / str(relative)).read_bytes()).hexdigest()
        except OSError:
            missing.append(f"native audit source missing: {relative}")
            continue
        if digest != source.get("sha256"):
            bad.append(f"native audit source changed: {relative}")
    return bad, missing


def _turn_findings(
    agent: Path, turn: dict[str, Any], allowed: set[str] | None
) -> tuple[int, list[str], list[str]]:
    audit = turn.get("session_audit")
    if not isinstance(audit, dict):
        return 0, [], ["no native session audit"]
    log_dir = turn.get("log_dir")
    relative = PurePosixPath(log_dir) if isinstance(log_dir, str) else None
    if relative is None or relative.is_absolute() or ".." in relative.parts:
        return 0, [], ["no valid turn log directory"]
    bad, missing = _audit_findings(audit, allowed, agent / str(relative))
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
    a: Audit,
    step: str,
    violations: dict[str, str],
    unmeasured: list[str],
    messages: dict[str, list[dict[str, str]]],
) -> int:
    agent = a.trial_dir / "steps" / step / "agent"
    workflow = _json(agent / WORKFLOW_FILE)
    turns = workflow.get("turns") if isinstance(workflow, dict) else None
    if not isinstance(turns, list) or not turns:
        unmeasured.append(f"{step}: no readable {WORKFLOW_FILE}")
        return 0
    allowed = _workspace_lines(a, step)
    for path, why in _broken_components(agent):
        violations.setdefault(f"component {path}: {why}", f"component {path}: {why} ({step})")
    checked = 0
    for turn in turns:
        label = f"{step}/{turn.get('stage')}/{turn.get('turn')}"
        records, bad, missing = _turn_findings(agent, turn, allowed)
        checked += records
        for finding in bad:
            violations.setdefault(finding, f"{finding} ({label})")
        unmeasured += [f"{label}: {reason}" for reason in missing]
        _record_messages(turn.get("session_audit"), messages)
    return checked


def check_p8(a: Audit) -> HealthCheck:
    if a.record.agent_override:
        return make_check(
            "P8", UNMEASURED, f"agent override {a.record.agent_override!r} runs no native session"
        )
    violations: dict[str, str] = {}
    unmeasured: list[str] = []
    messages: dict[str, list[dict[str, str]]] = {}
    checked = sum(_step_findings(a, s.step, violations, unmeasured, messages) for s in a.attempted)
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
