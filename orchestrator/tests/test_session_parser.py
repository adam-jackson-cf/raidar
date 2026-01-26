"""Tests for session log parsing across harnesses."""

import json
from pathlib import Path

from agentic_eval.parser.session_log import parse_cursor_session, parse_session


def _write_jsonl(tmp_dir: Path, name: str, lines: list[dict]) -> None:
    file_path = tmp_dir / name
    with file_path.open("w") as f:
        for entry in lines:
            f.write(json.dumps(entry))
            f.write("\n")


def _write_json(tmp_dir: Path, name: str, payload: list[dict]) -> None:
    (tmp_dir / name).write_text(json.dumps(payload))


def test_parse_cursor_session_jsonl(tmp_path):
    """Cursor parser should convert JSONL records into multiple event types."""
    _write_jsonl(
        tmp_path,
        "session.jsonl",
        [
            {"timestamp": "2024-01-01T00:00:00Z", "role": "user", "text": "Open the project"},
            {"timestamp": "2024-01-01T00:00:01Z", "event": "assistant", "text": "On it."},
            {"timestamp": "2024-01-01T00:00:02Z", "command": "bun test"},
            {"timestamp": "2024-01-01T00:00:03Z", "file_path": "src/app.tsx"},
        ],
    )

    events = parse_cursor_session(tmp_path)
    event_types = [event.event_type for event in events]

    assert "user_prompt" in event_types
    assert "assistant_message" in event_types
    assert "bash_command" in event_types
    assert "file_change" in event_types


def test_parse_copilot_session_gate_and_tool(tmp_path):
    """Copilot parser should capture gate results and tool calls."""
    _write_json(
        tmp_path,
        "copilot.json",
        [
            {
                "timestamp": "2024-01-01T00:05:00Z",
                "tool": "npm",
                "args": {"command": "run build"},
            },
            {
                "timestamp": "2024-01-01T00:05:05Z",
                "stdout": "ok",
                "stderr": "",
                "status": "success",
            },
        ],
    )

    events = parse_session(tmp_path, "copilot")
    tool_events = [e for e in events if e.event_type == "tool_call"]
    gate_events = [e for e in events if e.event_type == "gate_result"]

    assert len(tool_events) == 1
    assert tool_events[0].data["name"] == "npm"
    assert len(gate_events) == 1
    assert gate_events[0].data["status"] == "success"


def test_parse_session_unknown_harness(tmp_path):
    """Unknown harness should return empty event list."""
    events = parse_session(tmp_path, "claude-code")
    # Empty directory, but parser should still return sorted list (maybe empty)
    assert events == []
