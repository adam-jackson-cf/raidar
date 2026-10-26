"""Check ``P8``: the agent saw only its workflow's skills and instructions.

Codex records, at the head of every session, the skill roots it found and the ``AGENTS.md`` text
it merged, plus any plugin recommendation. Over every turn rollout of every attempted step this
check requires that:

- every skill root lies inside the workspace (``/app``): the codex home, its bundled ``.system``
  skills, a plugin cache, ``$HOME/.agents/skills`` and ``/etc/codex/skills`` are violations;
- no ``<recommended_plugins>`` message reached the agent;
- every non-blank ``<INSTRUCTIONS>`` line is a line of the workspace's own ``AGENTS.md`` or
  ``AGENTS.override.md`` (codex's ``--- project-doc ---`` separator aside), read from the step's
  stored ``/app`` snapshot and the one before it (the state the session started from).

Files a workflow places for the agent (``placement.skills: .agents/skills``, instructions) go
into the workspace, so the workspace rule covers them; nothing else is allowed. A stage's own
text in the workspace's instruction files (the verify gate's rules block) is taken out of the
graded snapshot, so its lines are read from the step's records instead: every file listed in
the workflow record's ``instructions``.

A step without ``workflow/workflow.json``, a turn without a rollout, an ``AGENTS.md`` message
without a stored snapshot, or an oracle/nop agent override (no codex ran) is ``unmeasured``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from raidar.contract.snapshot import APP_SOURCE
from raidar.engine.health.base import Audit, make_check
from raidar.engine.health.model import PASS, UNMEASURED, VIOLATION, HealthCheck

WORKFLOW_FILE = "workflow/workflow.json"
SKILL_ROOT = re.compile(r"^- `r\d+` = `(?P<path>[^`]+)`$", re.MULTILINE)
INSTRUCTIONS = re.compile(r"<INSTRUCTIONS>(?P<text>.*?)</INSTRUCTIONS>", re.DOTALL)
SKILLS_BLOCK = "<skills_instructions>"
PLUGINS_BLOCK = "<recommended_plugins>"
AGENTS_BLOCK = "# AGENTS.md instructions for "
AGENTS_FILES = ("AGENTS.md", "AGENTS.override.md")
CODEX_SEPARATORS = frozenset({"--- project-doc ---"})
_SHOWN = 3


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _texts(rollout: Path) -> Iterator[str]:
    """The text of every message in a codex rollout (``response_item`` rows)."""
    for line in rollout.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        for part in payload.get("content") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str):
                yield text


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
    return {line for path in files for line in _file_lines(path)}


def _instructions(text: str, allowed: set[str] | None) -> tuple[list[str], list[str]]:
    """(violations, unmeasured reasons) of one ``AGENTS.md`` instructions message."""
    merged = INSTRUCTIONS.search(text)
    if merged is None:
        return [], ["an AGENTS.md message has no <INSTRUCTIONS> block"]
    if allowed is None:
        return [], ["no stored /app snapshot to read the workspace AGENTS.md from"]
    foreign = [
        line.strip()
        for line in merged["text"].splitlines()
        if line.strip() and line.strip() not in allowed | CODEX_SEPARATORS
    ]
    if not foreign:
        return [], []
    return [
        f"instructions from outside the workspace: {foreign[0][:60]!r} "
        f"and {len(foreign) - 1} more line(s)"
    ], []


def _message(text: str, allowed: set[str] | None) -> tuple[list[str], list[str]]:
    """(violations, unmeasured reasons) of one message."""
    if text.startswith(PLUGINS_BLOCK):
        return ["plugin recommendations reached the agent"], []
    if text.startswith(SKILLS_BLOCK):
        roots = SKILL_ROOT.findall(text)
        return [
            f"skill root outside the workspace: {root}"
            for root in roots
            if not PurePosixPath(root).is_relative_to(APP_SOURCE)
        ], []
    if text.startswith(AGENTS_BLOCK):
        return _instructions(text, allowed)
    return [], []


def _turn_logs(agent: Path) -> list[tuple[str, list[Path]]] | None:
    """(label, rollouts) per workflow turn, or ``None`` without a readable workflow record."""
    workflow = _json(agent / WORKFLOW_FILE)
    turns = workflow.get("turns") if isinstance(workflow, dict) else None
    if not isinstance(turns, list) or not turns:
        return None
    found = []
    for turn in turns:
        turn = turn if isinstance(turn, dict) else {}
        label = f"{turn.get('stage')}/{turn.get('turn')}"
        log_dir = turn.get("log_dir")
        sessions = agent / log_dir / "sessions" if isinstance(log_dir, str) else None
        rollouts = sorted(sessions.rglob("rollout-*.jsonl")) if sessions else []
        found.append((label, rollouts))
    return found


def _step_findings(a: Audit, step: str, violations: dict[str, str], unmeasured: list[str]) -> int:
    """Check one step's rollouts into ``violations``/``unmeasured``; the rollouts checked."""
    turns = _turn_logs(a.trial_dir / "steps" / step / "agent")
    if turns is None:
        unmeasured.append(f"{step}: no readable {WORKFLOW_FILE}")
        return 0
    allowed = _workspace_lines(a, step)
    checked = 0
    for label, rollouts in turns:
        if not rollouts:
            unmeasured.append(f"{step}/{label}: no rollout recorded")
        for rollout in rollouts:
            checked += 1
            for text in _texts(rollout):
                bad, missing = _message(text, allowed)
                for finding in bad:
                    violations.setdefault(finding, f"{finding} ({step}/{label})")
                unmeasured += [f"{step}/{label}: {m}" for m in missing]
    return checked


def check_p8(a: Audit) -> HealthCheck:
    if a.record.agent_override:
        return make_check(
            "P8", UNMEASURED, f"agent override {a.record.agent_override!r} runs no codex session"
        )
    violations: dict[str, str] = {}
    unmeasured: list[str] = []
    checked = sum(_step_findings(a, s.step, violations, unmeasured) for s in a.attempted)
    unmeasured = list(dict.fromkeys(unmeasured))
    if violations:
        found = list(violations.values())
        more = f"; and {len(found) - _SHOWN} more" if len(found) > _SHOWN else ""
        detail = "; ".join(found[:_SHOWN]) + more
        return make_check("P8", VIOLATION, detail, findings=found, unmeasured=unmeasured)
    if unmeasured or not checked:
        detail = "; ".join(unmeasured) or "no attempted step"
        return make_check("P8", UNMEASURED, detail, unmeasured=unmeasured)
    return make_check(
        "P8", PASS, f"{checked} rollout(s) saw only workspace skills and instructions"
    )
