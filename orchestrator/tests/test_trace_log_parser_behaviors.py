import json
from pathlib import Path

from raidar.parser import trace_log


def test_json_record_readers_skip_invalid_and_accept_lists_events_and_objects(tmp_path):
    jsonl = tmp_path / "events.jsonl"
    jsonl.write_text('\n{"type":"assistant","content":"ok"}\nnot-json\n[]\n', encoding="utf-8")
    assert list(trace_log._read_jsonl_records(jsonl)) == [{"type": "assistant", "content": "ok"}]

    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps([{"a": 1}, "bad", {"b": 2}]), encoding="utf-8")
    assert trace_log._read_json_records(as_list) == [{"a": 1}, {"b": 2}]

    as_events = tmp_path / "events.json"
    as_events.write_text(json.dumps({"events": [{"c": 3}, "bad"]}), encoding="utf-8")
    assert trace_log._read_json_records(as_events) == [{"c": 3}]

    as_object = tmp_path / "object.json"
    as_object.write_text(json.dumps({"d": 4}), encoding="utf-8")
    assert trace_log._read_json_records(as_object) == [{"d": 4}]

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    assert trace_log._read_json_records(invalid) == []


def test_structured_records_parse_timestamps_values_tools_messages_and_gates(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    (trace_dir / "a.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "timestamp": 1,
                        "role": "user",
                        "text": "hello",
                        "command": "bun run build",
                        "path": "src/app.ts",
                    },
                    {
                        "created_at": "2026-01-01T00:00:00",
                        "event": "gate",
                        "status": "failed",
                        "stdout": "o" * 600,
                        "stderr": "err",
                    },
                    {
                        "time": "2026-01-01T00:00:01",
                        "tool_name": "Read",
                        "payload": {"file": "a"},
                        "message": "assistant text",
                    },
                    {"ts": "2026-01-01T00:00:02", "tool": "Shell", "args": "bun"},
                ]
            }
        ),
        encoding="utf-8",
    )

    events = trace_log._parse_structured_cli_trace(trace_dir, ("*.json",), "assistant")

    assert [event.event_type for event in events] == [
        "bash_command",
        "file_change",
        "user_prompt",
        "gate_result",
        "tool_call",
        "assistant_message",
        "tool_call",
    ]
    gate = next(event for event in events if event.event_type == "gate_result")
    assert gate.data["stdout"].endswith("...")
    assert events[-1].data["input"] == {"value": "bun"}
    assert trace_log._coerce_timestamp({"ts": object()})


def test_codex_trace_parser_maps_supported_entries_and_ignores_invalid(tmp_path):
    trace_dir = tmp_path / "codex"
    trace_dir.mkdir()
    (trace_dir / "trace.jsonl").write_text(
        "\n".join(
            [
                "{",
                json.dumps({"type": "user", "timestamp": "1", "content": "u"}),
                json.dumps({"type": "assistant", "timestamp": "2", "content": "a"}),
                json.dumps({"type": "command", "timestamp": "3", "command": "bun run test"}),
                json.dumps(
                    {
                        "type": "tool_call",
                        "timestamp": "4",
                        "tool_name": "Read",
                        "arguments": {"file": "x"},
                    }
                ),
                json.dumps({"type": "file_change", "timestamp": "5", "file_path": "x"}),
                json.dumps({"type": "unknown", "timestamp": "6"}),
            ]
        ),
        encoding="utf-8",
    )

    events = trace_log.parse_codex_trace(trace_dir)

    assert [event.event_type for event in events] == [
        "user_prompt",
        "assistant_message",
        "bash_command",
        "tool_call",
        "file_change",
    ]
    assert trace_log.parse_codex_entry({"type": "unknown"}) is None


def test_parse_trace_routes_supported_harnesses_and_support_flag(tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    (trace_dir / "events.json").write_text(json.dumps({"text": "assistant"}), encoding="utf-8")

    assert trace_log.parse_trace("unknown", trace_dir) == []
    assert trace_log.parser_supports_structured_traces("codex-cli") is False
    assert trace_log.parser_supports_structured_traces("gemini") is True
    for harness in ("claude-code", "gemini", "cursor", "copilot", "pi"):
        events = trace_log.parse_trace(harness, trace_dir)
        assert events[0].event_type == "assistant_message"


def test_iter_structured_records_ignores_unreadable_files(monkeypatch, tmp_path):
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    (trace_dir / "events.json").write_text("{}", encoding="utf-8")

    def fail_json(_path: Path):
        raise OSError("nope")

    monkeypatch.setattr(trace_log, "_read_json_records", fail_json)

    assert list(trace_log._iter_structured_records(trace_dir, ("*.json",))) == []
