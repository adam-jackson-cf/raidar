"""Trace log parsing for different CLI tools."""

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..schemas.events import TraceEvent

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
    with open(file_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def _read_json_records(file_path: Path) -> Iterable[dict]:
    try:
        with open(file_path, encoding="utf-8") as handle:
            data = json.load(handle)
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


def _iter_structured_records(trace_dir: Path, patterns: Iterable[str]) -> Iterable[dict]:
    for pattern in patterns:
        for file_path in trace_dir.glob(pattern):
            try:
                if file_path.suffix == ".jsonl":
                    yield from _read_jsonl_records(file_path)
                else:
                    yield from _read_json_records(file_path)
            except OSError:
                continue


def _coerce_timestamp(entry: dict) -> str:
    for key in ("timestamp", "time", "created_at", "ts"):
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value).isoformat()
            except ValueError:
                continue
        if isinstance(value, str):
            return value
    return datetime.now().isoformat()


def _append_command_event(events: list[TraceEvent], timestamp: str, command: str | None) -> None:
    if not command:
        return
    events.append(
        TraceEvent(timestamp=timestamp, event_type="bash_command", data={"command": command})
    )


def _append_file_event(events: list[TraceEvent], timestamp: str, file_path: str | None) -> None:
    if not file_path:
        return
    events.append(
        TraceEvent(timestamp=timestamp, event_type="file_change", data={"file_path": file_path})
    )


def _append_gate_event(
    events: list[TraceEvent],
    timestamp: str,
    role_hint: str,
    status: str | None,
    stdout: str | None,
    stderr: str | None,
) -> None:
    if not stdout and not stderr and role_hint not in {"gate", "verification"}:
        return
    events.append(
        TraceEvent(
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
    events: list[TraceEvent],
    timestamp: str,
    tool_name: str | None,
    tool_args: object | None,
) -> None:
    if not tool_name:
        return
    events.append(
        TraceEvent(
            timestamp=timestamp,
            event_type="tool_call",
            data={
                "name": tool_name,
                "input": tool_args if isinstance(tool_args, dict) else {"value": tool_args},
            },
        )
    )


def _append_message_event(
    events: list[TraceEvent],
    timestamp: str,
    role_hint: str,
    text: object | None,
) -> None:
    if not text:
        return
    event_type = "user_prompt" if role_hint in {"user", "human", "prompt"} else "assistant_message"
    events.append(
        TraceEvent(
            timestamp=timestamp,
            event_type=event_type,
            data={"content": truncate_content(str(text))},
        )
    )


def _structured_record_to_events(entry: dict, default_role: str) -> list[TraceEvent]:
    timestamp = _coerce_timestamp(entry)
    events: list[TraceEvent] = []
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


def _parse_structured_cli_trace(
    trace_dir: Path,
    patterns: Iterable[str],
    default_role: str,
) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for entry in _iter_structured_records(trace_dir, patterns):
        events.extend(_structured_record_to_events(entry, default_role))
    return sorted(events, key=lambda event: event.timestamp)


def parse_codex_trace(trace_dir: Path) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for jsonl_file in trace_dir.glob("*.jsonl"):
        with open(jsonl_file, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = parse_codex_entry(entry)
                if event is not None:
                    events.append(event)
    return sorted(events, key=lambda event: event.timestamp)


def parse_codex_entry(entry: dict) -> TraceEvent | None:
    event_type = entry.get("type")
    timestamp = entry.get("timestamp", datetime.now().isoformat())
    if event_type == "user":
        return TraceEvent(
            timestamp=timestamp,
            event_type="user_prompt",
            data={"content": truncate_content(entry.get("content", ""))},
        )
    if event_type == "assistant":
        return TraceEvent(
            timestamp=timestamp,
            event_type="assistant_message",
            data={"content": truncate_content(entry.get("content", ""))},
        )
    if event_type == "command":
        return TraceEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": entry.get("command", "")},
        )
    if event_type == "tool_call":
        return TraceEvent(
            timestamp=timestamp,
            event_type="tool_call",
            data={
                "name": entry.get("tool_name", ""),
                "input": entry.get("arguments", {}),
            },
        )
    if event_type == "file_change":
        return TraceEvent(
            timestamp=timestamp,
            event_type="file_change",
            data={"file_path": entry.get("file_path", "")},
        )
    return None


def parse_claude_trace(trace_dir: Path) -> list[TraceEvent]:
    return _parse_structured_cli_trace(trace_dir, ("*.jsonl", "*.json"), "assistant")


def parse_gemini_trace(trace_dir: Path) -> list[TraceEvent]:
    return _parse_structured_cli_trace(trace_dir, ("*.jsonl", "*.json"), "assistant")


def parse_cursor_trace(trace_dir: Path) -> list[TraceEvent]:
    return _parse_structured_cli_trace(trace_dir, ("*.jsonl", "*.json"), "assistant")


def parse_copilot_trace(trace_dir: Path) -> list[TraceEvent]:
    return _parse_structured_cli_trace(trace_dir, ("*.jsonl", "*.json"), "assistant")


def parse_pi_trace(trace_dir: Path) -> list[TraceEvent]:
    return _parse_structured_cli_trace(trace_dir, ("*.jsonl", "*.json"), "assistant")


def parse_trace(agent: str, trace_dir: Path) -> list[TraceEvent]:
    parsers: dict[str, callable] = {
        "codex-cli": parse_codex_trace,
        "claude-code": parse_claude_trace,
        "gemini": parse_gemini_trace,
        "cursor": parse_cursor_trace,
        "copilot": parse_copilot_trace,
        "pi": parse_pi_trace,
    }
    parser = parsers.get(agent)
    if parser is None:
        return []
    return parser(trace_dir)


def parser_supports_structured_traces(agent: str) -> bool:
    return agent in {"claude-code", "gemini", "cursor", "copilot", "pi"}


TraceFormat = Literal["json", "jsonl"]
