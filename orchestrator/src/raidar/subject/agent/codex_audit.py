"""What codex recorded of one turn: its final text and the session audit the isolation check reads.

Codex records, at the head of every session rollout (``sessions/**/rollout-*.jsonl``), the skill
roots it found (a ``<skills_instructions>`` message) and the ``AGENTS.md`` text it merged (an
``# AGENTS.md instructions for <dir>`` message with an ``<INSTRUCTIONS>`` block), plus any plugin
recommendation (``<recommended_plugins>``). The turn's final text is the last ``agent_message``
item of the turn's own ``--json`` stream (``codex.txt``, rewritten by every turn), not of the
rollouts, which hold every turn of the session.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from raidar.subject.agent.protocol import digest, session_audit_record, source_record

HARNESS = "codex"
STREAM_FILE = "codex.txt"
SESSIONS_DIR = "sessions"
SKILL_ROOT = re.compile(r"^- `r\d+` = `(?P<path>[^`]+)`$", re.MULTILINE)
INSTRUCTIONS = re.compile(r"<INSTRUCTIONS>(?P<text>.*?)</INSTRUCTIONS>", re.DOTALL)
SKILLS_BLOCK = "<skills_instructions>"
PLUGINS_BLOCK = "<recommended_plugins>"
AGENTS_BLOCK = "# AGENTS.md instructions for "
DIGESTED_ROLES = frozenset({"developer", "system"})


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _message_texts(payload: dict[str, Any]) -> list[str]:
    parts = payload.get("content")
    return [
        part["text"]
        for part in (parts if isinstance(parts, list) else [])
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]


def _row_version(row: dict[str, Any]) -> str | None:
    payload = row.get("payload")
    if row.get("type") != "session_meta" or not isinstance(payload, dict):
        return None
    found = payload.get("cli_version")
    return found if isinstance(found, str) else None


def _row_messages(row: dict[str, Any]) -> Iterator[tuple[Any, str]]:
    """``(role, text)`` of every message part of a rollout row."""
    payload = row.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        for text in _message_texts(payload):
            yield payload.get("role"), text


def codex_session_audit(turn_dir: Path) -> dict[str, Any]:
    """The normalized audit of the codex rollouts under ``turn_dir/sessions`` (module docstring)."""
    rollouts = sorted((turn_dir / SESSIONS_DIR).rglob("rollout-*.jsonl"))
    version: str | None = None
    records = 0
    roots: list[str] = []
    instructions: list[dict[str, str | None]] = []
    messages: list[dict[str, str]] = []
    violations: list[str] = []
    unmeasured: list[str] = []
    if not rollouts:
        unmeasured.append("no rollout recorded")
    for rollout in rollouts:
        for row in _rows(rollout):
            records += 1
            version = version or _row_version(row)
            for role, text in _row_messages(row):
                if role in DIGESTED_ROLES:
                    messages.append({"role": str(role), "sha256": digest(text)})
                _classify(text, roots, instructions, violations, unmeasured)
    return session_audit_record(
        HARNESS,
        version=version,
        records=records,
        sources=[source_record(turn_dir, rollout) for rollout in rollouts],
        skill_roots=roots,
        instructions=instructions,
        messages=messages,
        violations=violations,
        unmeasured=unmeasured,
    )


def _classify(
    text: str,
    roots: list[str],
    instructions: list[dict[str, str | None]],
    violations: list[str],
    unmeasured: list[str],
) -> None:
    if text.startswith(PLUGINS_BLOCK):
        violations.append("plugin recommendations reached the agent")
    elif text.startswith(SKILLS_BLOCK):
        roots.extend(SKILL_ROOT.findall(text))
    elif text.startswith(AGENTS_BLOCK):
        merged = INSTRUCTIONS.search(text)
        if merged is None:
            unmeasured.append("an AGENTS.md message has no <INSTRUCTIONS> block")
            return
        folder = text.removeprefix(AGENTS_BLOCK).split("\n", 1)[0].strip() or None
        text = "".join(
            line
            for line in merged["text"].splitlines(keepends=True)
            if line.strip() != "--- project-doc ---"
        )
        instructions.append({"path": folder, "text": text})


def codex_last_message(turn_dir: Path) -> str | None:
    """The turn's last ``agent_message`` text from its own ``--json`` stream, else ``None``."""
    try:
        stream = (turn_dir / STREAM_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found: str | None = None
    for line in stream.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        text = _agent_message(event) if isinstance(event, dict) else None
        if text is not None:
            found = text
    return found


def _agent_message(event: dict[str, Any]) -> str | None:
    item = event.get("item")
    if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "agent_message"
        and isinstance(item.get("text"), str)
    ):
        return item["text"]
    return None
