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
                    entry_events = parse_claude_entry(entry)
                    events.extend(entry_events)
                except json.JSONDecodeError:
                    continue

    return sorted(events, key=lambda e: e.timestamp)


def _create_tool_event(timestamp: str, tool_name: str, tool_input: dict) -> SessionEvent:
    """Create a SessionEvent for a tool use block."""
    if tool_name == "Bash":
        return SessionEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": tool_input.get("command", "")},
        )
    if tool_name in ("Write", "Edit"):
        return SessionEvent(
            timestamp=timestamp,
            event_type="file_change",
            data={"file_path": tool_input.get("file_path", "")},
        )
    return SessionEvent(
        timestamp=timestamp,
        event_type="tool_call",
        data={"name": tool_name, "input": tool_input},
    )


def _parse_claude_user_content(timestamp: str, content: list | str) -> list[SessionEvent]:
    """Parse user content blocks from Claude Code entry."""
    events: list[SessionEvent] = []

    if isinstance(content, str):
        events.append(
            SessionEvent(
                timestamp=timestamp,
                event_type="user_prompt",
                data={"content": truncate_content(content)},
            )
        )
        return events

    text_parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_result":
            events.append(
                SessionEvent(
                    timestamp=timestamp,
                    event_type="gate_result",
                    data={
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": truncate_content(str(block.get("content", ""))),
                    },
                )
            )

    if text_parts:
        events.append(
            SessionEvent(
                timestamp=timestamp,
                event_type="user_prompt",
                data={"content": truncate_content("".join(text_parts))},
            )
        )

    return events


def _parse_claude_assistant_content(timestamp: str, content: list) -> list[SessionEvent]:
    """Parse assistant content blocks from Claude Code entry."""
    events: list[SessionEvent] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "tool_use":
            events.append(
                _create_tool_event(timestamp, block.get("name", ""), block.get("input", {}))
            )
        elif block_type == "text":
            text = block.get("text", "")
            if text.strip():
                events.append(
                    SessionEvent(
                        timestamp=timestamp,
                        event_type="assistant_message",
                        data={"content": truncate_content(text)},
                    )
                )

    return events


def parse_claude_entry(entry: dict) -> list[SessionEvent]:
    """Parse a single Claude Code log entry.

    Returns a list of events since a single entry can contain multiple blocks.
    """
    timestamp = entry.get("timestamp", datetime.now().isoformat())
    role = entry.get("role")
    content = entry.get("content", [])

    if role == "user":
        return _parse_claude_user_content(timestamp, content)

    if role == "assistant" and isinstance(content, list):
        return _parse_claude_assistant_content(timestamp, content)

    return []


def parse_gemini_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Gemini CLI session logs.

    Gemini stores sessions in ~/.gemini/sessions/*.json

    Args:
        session_dir: Path to session directory

    Returns:
        List of session events
    """
    events: list[SessionEvent] = []

    for json_file in session_dir.glob("*.json"):
        try:
            with open(json_file) as f:
                session_data = json.load(f)

            # Gemini session structure has 'contents' array
            contents = session_data.get("contents", [])
            for entry in contents:
                entry_events = parse_gemini_entry(entry)
                events.extend(entry_events)
        except (json.JSONDecodeError, OSError):
            continue

    return sorted(events, key=lambda e: e.timestamp)


def parse_gemini_entry(entry: dict) -> list[SessionEvent]:
    """Parse a single Gemini session entry.

    Gemini entries have 'role' and 'parts' array with text, function_call, or function_response.
    """
    timestamp = entry.get("timestamp", datetime.now().isoformat())
    role = entry.get("role", "")
    parts = entry.get("parts", [])
    events: list[SessionEvent] = []

    for part in parts:
        if isinstance(part, dict):
            # Text content
            if "text" in part:
                event_type = "user_prompt" if role == "user" else "assistant_message"
                events.append(
                    SessionEvent(
                        timestamp=timestamp,
                        event_type=event_type,
                        data={"content": truncate_content(part["text"])},
                    )
                )

            # Function call (tool use)
            elif "functionCall" in part:
                func_call = part["functionCall"]
                func_name = func_call.get("name", "")
                func_args = func_call.get("args", {})

                # Map Gemini function names to event types
                if func_name in ("run_shell_command", "execute_command"):
                    events.append(
                        SessionEvent(
                            timestamp=timestamp,
                            event_type="bash_command",
                            data={"command": func_args.get("command", "")},
                        )
                    )
                elif func_name in ("write_file", "edit_file", "create_file"):
                    events.append(
                        SessionEvent(
                            timestamp=timestamp,
                            event_type="file_change",
                            data={
                                "file_path": func_args.get("path", func_args.get("file_path", ""))
                            },
                        )
                    )
                else:
                    events.append(
                        SessionEvent(
                            timestamp=timestamp,
                            event_type="tool_call",
                            data={"name": func_name, "args": func_args},
                        )
                    )

            # Function response (tool result)
            elif "functionResponse" in part:
                func_response = part["functionResponse"]
                events.append(
                    SessionEvent(
                        timestamp=timestamp,
                        event_type="gate_result",
                        data={
                            "name": func_response.get("name", ""),
                            "response": truncate_content(str(func_response.get("response", {}))),
                        },
                    )
                )

        elif isinstance(part, str):
            # Simple string text
            event_type = "user_prompt" if role == "user" else "assistant_message"
            events.append(
                SessionEvent(
                    timestamp=timestamp,
                    event_type=event_type,
                    data={"content": truncate_content(part)},
                )
            )

    return events


def parse_session(
    session_dir: Path,
    harness: Literal["codex-cli", "claude-code", "gemini"],
) -> list[SessionEvent]:
    """Parse session logs for the specified harness.

    Args:
        session_dir: Path to session directory
        harness: Harness type

    Returns:
        List of session events
    """
    if harness == "codex-cli":
        return parse_codex_session(session_dir)
    elif harness == "claude-code":
        return parse_claude_session(session_dir)
    elif harness == "gemini":
        return parse_gemini_session(session_dir)
    else:
        # Other harnesses may need custom parsers
        return []
