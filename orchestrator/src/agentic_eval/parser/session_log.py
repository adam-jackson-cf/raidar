"""Session log parsing for different CLI tools."""

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..schemas.events import SessionEvent

ROLE_KEYS = ("role", "speaker", "source", "event", "type")
TEXT_KEYS = ("text", "content", "message")
COMMAND_KEYS = ("command", "cmd")
FILE_KEYS = ("file_path", "path")
TOOL_KEYS = ("tool", "tool_name")
TOOL_ARGS_KEYS = ("args", "payload", "data")


def _first_truthy(entry: dict, keys: Iterable[str]):
    for key in keys:
        value = entry.get(key)
        if value:
            return value
    return None


def truncate_content(content: str, max_length: int = 500) -> str:
    """Truncate content for storage."""
    if len(content) <= max_length:
        return content
    return content[:max_length] + "..."


def _read_jsonl_records(file_path: Path) -> Iterable[dict]:
    """Read JSONL file and yield each entry."""
    with open(file_path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def _read_json_records(file_path: Path) -> Iterable[dict]:
    """Read JSON file (list or dict) and yield entries."""
    try:
        with open(file_path) as f:
            data = json.load(f)
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]

    if isinstance(data, dict):
        events = data.get("events")
        if isinstance(events, list):
            return [entry for entry in events if isinstance(entry, dict)]
        return [data]

    return []


def _iter_structured_records(session_dir: Path, patterns: Iterable[str]) -> Iterable[dict]:
    """Yield JSON objects from files matching the provided patterns."""
    for pattern in patterns:
        for file_path in session_dir.glob(pattern):
            try:
                if file_path.suffix == ".jsonl":
                    yield from _read_jsonl_records(file_path)
                else:
                    yield from _read_json_records(file_path)
            except OSError:
                continue


def _coerce_timestamp(entry: dict) -> str:
    """Extract timestamp or default to now."""
    for key in ("timestamp", "time", "created_at", "ts"):
        if key in entry:
            value = entry[key]
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(value).isoformat()
                except ValueError:
                    continue
            if isinstance(value, str):
                return value
    return datetime.now().isoformat()


def _structured_record_to_events(entry: dict, default_role: str) -> list[SessionEvent]:
    """Convert a generic CLI log entry into SessionEvents."""
    timestamp = _coerce_timestamp(entry)
    events: list[SessionEvent] = []

    role_hint = str(_first_truthy(entry, ROLE_KEYS) or default_role).lower()

    text = _first_truthy(entry, TEXT_KEYS)
    command = _first_truthy(entry, COMMAND_KEYS)
    file_path = _first_truthy(entry, FILE_KEYS)
    stdout = entry.get("stdout")
    stderr = entry.get("stderr")
    status = entry.get("status")
    tool_name = _first_truthy(entry, TOOL_KEYS)
    tool_args = _first_truthy(entry, TOOL_ARGS_KEYS)

    _append_command_event(events, timestamp, command)
    _append_file_event(events, timestamp, file_path)
    _append_gate_event(events, timestamp, role_hint, status, stdout, stderr)
    _append_tool_event(events, timestamp, tool_name, tool_args)
    _append_message_event(events, timestamp, role_hint, text)

    return events


def _append_command_event(events: list[SessionEvent], timestamp: str, command: str | None) -> None:
    if not command:
        return
    events.append(
        SessionEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": command},
        )
    )


def _append_file_event(events: list[SessionEvent], timestamp: str, file_path: str | None) -> None:
    if not file_path:
        return
    events.append(
        SessionEvent(
            timestamp=timestamp,
            event_type="file_change",
            data={"file_path": file_path},
        )
    )


def _append_gate_event(
    events: list[SessionEvent],
    timestamp: str,
    role_hint: str,
    status: str | None,
    stdout: str | None,
    stderr: str | None,
) -> None:
    if not stdout and not stderr and role_hint not in {"gate", "verification"}:
        return
    events.append(
        SessionEvent(
            timestamp=timestamp,
            event_type="gate_result",
            data={
                "status": status,
                "stdout": truncate_content(stdout or ""),
                "stderr": truncate_content(stderr or ""),
            },
        )
    )


def _append_tool_event(
    events: list[SessionEvent],
    timestamp: str,
    tool_name: str | None,
    tool_args: object | None,
) -> None:
    if not tool_name:
        return
    events.append(
        SessionEvent(
            timestamp=timestamp,
            event_type="tool_call",
            data={
                "name": tool_name,
                "input": tool_args if isinstance(tool_args, dict) else {"value": tool_args},
            },
        )
    )


def _append_message_event(
    events: list[SessionEvent],
    timestamp: str,
    role_hint: str,
    text: object | None,
) -> None:
    if not text:
        return
    event_type = "user_prompt" if role_hint in {"user", "human", "prompt"} else "assistant_message"
    events.append(
        SessionEvent(
            timestamp=timestamp,
            event_type=event_type,
            data={"content": truncate_content(str(text))},
        )
    )


def _parse_structured_cli_session(
    session_dir: Path,
    patterns: Iterable[str],
    default_role: str,
) -> list[SessionEvent]:
    """Parse CLI logs where entries are structured JSON records."""
    events: list[SessionEvent] = []
    for entry in _iter_structured_records(session_dir, patterns):
        events.extend(_structured_record_to_events(entry, default_role))
    return sorted(events, key=lambda e: e.timestamp)


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
        events.extend(_gemini_part_to_events(timestamp, role, part))

    return events


def _gemini_event_type(role: str) -> str:
    return "user_prompt" if role == "user" else "assistant_message"


def _gemini_text_event(timestamp: str, role: str, text: str) -> SessionEvent:
    return SessionEvent(
        timestamp=timestamp,
        event_type=_gemini_event_type(role),
        data={"content": truncate_content(text)},
    )


def _gemini_function_call_events(timestamp: str, func_call: dict) -> list[SessionEvent]:
    func_name = func_call.get("name", "")
    func_args = func_call.get("args", {})

    if func_name in ("run_shell_command", "execute_command"):
        return [
            SessionEvent(
                timestamp=timestamp,
                event_type="bash_command",
                data={"command": func_args.get("command", "")},
            )
        ]

    if func_name in ("write_file", "edit_file", "create_file"):
        return [
            SessionEvent(
                timestamp=timestamp,
                event_type="file_change",
                data={"file_path": func_args.get("path", func_args.get("file_path", ""))},
            )
        ]

    return [
        SessionEvent(
            timestamp=timestamp,
            event_type="tool_call",
            data={"name": func_name, "args": func_args},
        )
    ]


def _gemini_function_response_event(timestamp: str, func_response: dict) -> SessionEvent:
    return SessionEvent(
        timestamp=timestamp,
        event_type="gate_result",
        data={
            "name": func_response.get("name", ""),
            "response": truncate_content(str(func_response.get("response", {}))),
        },
    )


def _gemini_part_to_events(timestamp: str, role: str, part: object) -> list[SessionEvent]:
    if isinstance(part, dict):
        if "text" in part:
            return [_gemini_text_event(timestamp, role, part["text"])]

        if "functionCall" in part:
            return _gemini_function_call_events(timestamp, part["functionCall"])

        if "functionResponse" in part:
            return [_gemini_function_response_event(timestamp, part["functionResponse"])]

        return []

    if isinstance(part, str):
        return [_gemini_text_event(timestamp, role, part)]

    return []


def parse_cursor_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Cursor CLI session logs (JSON/JSONL)."""
    return _parse_structured_cli_session(
        session_dir,
        patterns=("*.jsonl", "*.json"),
        default_role="cursor",
    )


def parse_copilot_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Copilot CLI session logs."""
    return _parse_structured_cli_session(
        session_dir,
        patterns=("*.jsonl", "*.json"),
        default_role="copilot",
    )


def parse_pi_session(session_dir: Path) -> list[SessionEvent]:
    """Parse Pi agent CLI session logs."""
    return _parse_structured_cli_session(
        session_dir,
        patterns=("*.jsonl", "*.json"),
        default_role="pi",
    )


def parse_openhands_session(session_dir: Path) -> list[SessionEvent]:
    """Parse OpenHands harness session logs."""
    return _parse_structured_cli_session(
        session_dir,
        patterns=("*.jsonl", "*.json"),
        default_role="openhands",
    )


def parse_session(
    session_dir: Path,
    harness: Literal[
        "codex-cli",
        "claude-code",
        "gemini",
        "cursor",
        "copilot",
        "pi",
        "openhands",
    ],
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
    elif harness == "cursor":
        return parse_cursor_session(session_dir)
    elif harness == "copilot":
        return parse_copilot_session(session_dir)
    elif harness == "pi":
        return parse_pi_session(session_dir)
    elif harness == "openhands":
        return parse_openhands_session(session_dir)
    else:
        # Other harnesses may need custom parsers
        return []
