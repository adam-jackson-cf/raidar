"""Harness trace-event projection helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from raidar.runtime.command_records import _normalize_command
from raidar.runtime.harness_logs import _extract_item_completed, _read_jsonl_dicts
from raidar.schemas.events import TraceEvent


def _harness_emits_structured_trace_events(harness: str) -> bool:
    if harness == "codex-cli":
        return True
    if harness in {"claude-code", "gemini", "cursor", "copilot", "pi"}:
        return False
    raise ValueError(f"Unsupported harness for trace event extraction: {harness}")


def _events_from_command(timestamp: str, item: dict) -> list[TraceEvent]:
    command = _normalize_command(str(item.get("command", "")))
    return [
        TraceEvent(
            timestamp=timestamp,
            event_type="bash_command",
            data={"command": command},
        ),
        TraceEvent(
            timestamp=timestamp,
            event_type="gate_result",
            data={
                "status": item.get("status"),
                "exit_code": int(item.get("exit_code", 0) or 0),
            },
        ),
    ]


def _events_from_file_changes(timestamp: str, item: dict) -> list[TraceEvent]:
    file_events: list[TraceEvent] = []
    for change in item.get("changes", []) or []:
        path = change.get("path")
        if not path:
            continue
        file_events.append(
            TraceEvent(
                timestamp=timestamp,
                event_type="file_change",
                data={"file_path": str(path)},
            )
        )
    return file_events


def _events_from_item(timestamp: str, item: dict) -> list[TraceEvent]:
    item_type = item.get("type")
    if item_type == "command_execution":
        return _events_from_command(timestamp, item)
    if item_type == "file_change":
        return _events_from_file_changes(timestamp, item)
    if item_type != "agent_message":
        return []
    text = item.get("text")
    if not text:
        return []
    return [
        TraceEvent(
            timestamp=timestamp,
            event_type="assistant_message",
            data={"content": str(text)},
        )
    ]


def collect_trace_events(
    trial_dir: Path | None,
    *,
    harness: str,
) -> list[TraceEvent]:
    """Project harness logs into normalized trace events."""
    if not trial_dir:
        return []
    if not _harness_emits_structured_trace_events(harness):
        return []

    events: list[TraceEvent] = []
    for entry in _read_jsonl_dicts(trial_dir / "agent" / "codex.txt"):
        timestamp = str(entry.get("timestamp") or datetime.now(UTC).isoformat())
        item = _extract_item_completed(entry)
        if not item:
            continue
        events.extend(_events_from_item(timestamp, item))
    return events
