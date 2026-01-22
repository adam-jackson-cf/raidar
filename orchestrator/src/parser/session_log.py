"""Session log parsing for different CLI tools."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..schemas.events import SessionEvent


def truncate_content(content: str, max_length: int = 500) -> str:
    """Truncate content for storage."""
    if len(content) <= max_length:
        return content
    return content[:max_length] + "..."


def parse_codex_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Codex CLI session logs.

    Codex stores sessions in ~/.codex/sessions/YYYY/MM/DD/*.jsonl

    Args:
        session_dir: Path to session directory

    Returns:
        List of session events
    """
    events: list[SessionEvent] = []

    for jsonl_file in session_dir.glob("*.jsonl"):
        with open(jsonl_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    event = parse_codex_entry(entry)
                    if event:
                        events.append(event)
                except json.JSONDecodeError:
                    continue

    return sorted(events, key=lambda e: e.timestamp)


def parse_codex_entry(entry: dict) -> SessionEvent | None:
    """Parse a single Codex log entry."""
    event_type = entry.get("type")
    timestamp = entry.get("timestamp", datetime.now().isoformat())

    if event_type == "user":
        return SessionEvent(
            timestamp=timestamp,
            event_type="user_prompt",
            data={"content": truncate_content(entry.get("content", ""))},
        )
    elif event_type == "assistant":
        return SessionEvent(
            timestamp=timestamp,
            event_type="assistant_message",
            data={"content": truncate_content(entry.get("content", ""))},
        )
    elif event_type == "function_call":
        name = entry.get("name", "")
        if name in ("bash", "shell"):
            return SessionEvent(
                timestamp=timestamp,
                event_type="bash_command",
                data={"command": entry.get("arguments", {}).get("command", "")},
            )
        elif name in ("write_file", "edit_file"):
            return SessionEvent(
                timestamp=timestamp,
                event_type="file_change",
                data={"file_path": entry.get("arguments", {}).get("path", "")},
            )
        else:
            return SessionEvent(
                timestamp=timestamp,
                event_type="tool_call",
                data={"name": name, "arguments": entry.get("arguments", {})},
            )

    return None


def parse_claude_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Claude Code session logs.

    Claude Code stores sessions in ~/.claude/projects/{project}/sessions/*.jsonl

    Args:
        session_dir: Path to session directory

    Returns:
        List of session events
    """
    events: list[SessionEvent] = []

    for jsonl_file in session_dir.glob("*.jsonl"):
        with open(jsonl_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    event = parse_claude_entry(entry)
                    if event:
                        events.append(event)
                except json.JSONDecodeError:
                    continue

    return sorted(events, key=lambda e: e.timestamp)


def parse_claude_entry(entry: dict) -> SessionEvent | None:
    """Parse a single Claude Code log entry."""
    timestamp = entry.get("timestamp", datetime.now().isoformat())
    role = entry.get("role")
    content = entry.get("content", [])

    if role == "user":
        text_content = ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_content += block.get("text", "")
        elif isinstance(content, str):
            text_content = content

        return SessionEvent(
            timestamp=timestamp,
            event_type="user_prompt",
            data={"content": truncate_content(text_content)},
        )

    elif role == "assistant":
        # Look for tool use
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})

                        if tool_name == "Bash":
                            return SessionEvent(
                                timestamp=timestamp,
                                event_type="bash_command",
                                data={"command": tool_input.get("command", "")},
                            )
                        elif tool_name in ("Write", "Edit"):
                            return SessionEvent(
                                timestamp=timestamp,
                                event_type="file_change",
                                data={"file_path": tool_input.get("file_path", "")},
                            )
                        else:
                            return SessionEvent(
                                timestamp=timestamp,
                                event_type="tool_call",
                                data={"name": tool_name, "input": tool_input},
                            )
                    elif block.get("type") == "text":
                        return SessionEvent(
                            timestamp=timestamp,
                            event_type="assistant_message",
                            data={"content": truncate_content(block.get("text", ""))},
                        )

    return None


def parse_session(
    session_dir: Path,
    harness: Literal["codex", "claude-code", "gemini"],
) -> list[SessionEvent]:
    """Parse session logs for the specified harness.

    Args:
        session_dir: Path to session directory
        harness: Harness type

    Returns:
        List of session events
    """
    if harness == "codex":
        return parse_codex_session(session_dir)
    elif harness == "claude-code":
        return parse_claude_session(session_dir)
    else:
        # Other harnesses may need custom parsers
        return []
