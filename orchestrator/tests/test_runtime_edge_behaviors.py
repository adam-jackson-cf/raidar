import subprocess
from types import SimpleNamespace

import pytest

from raidar.run_metadata import uncached_input_tokens
from raidar.runtime import trace_events
from raidar.schemas.scenario import VerificationGate
from raidar.watcher.gate_watcher import GateWatcher, categorize_failure, truncate_output


def test_uncached_input_tokens_handles_missing_non_dict_and_numeric_metadata(sample_eval_run):
    sample_eval_run.scores.metadata["process"] = {"uncached_input_tokens": "42"}
    assert uncached_input_tokens(sample_eval_run) == 42

    sample_eval_run.scores.metadata["process"] = "not-a-dict"
    assert uncached_input_tokens(sample_eval_run) == 0

    sample_eval_run.scores.metadata.pop("process")
    assert uncached_input_tokens(sample_eval_run) == 0


def test_trace_event_support_is_explicit_by_harness():
    assert trace_events._harness_emits_structured_trace_events("codex-cli") is True
    for harness in ("claude-code", "gemini", "cursor", "copilot", "pi"):
        assert trace_events._harness_emits_structured_trace_events(harness) is False

    with pytest.raises(ValueError, match="Unsupported harness"):
        trace_events._harness_emits_structured_trace_events("unknown")


def test_trace_events_project_commands_files_and_messages(monkeypatch, tmp_path):
    entries = [
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "item": {"type": "command_execution", "command": "bun   run build", "status": "failed"},
        },
        {
            "timestamp": "2026-01-01T00:00:01+00:00",
            "item": {
                "type": "file_change",
                "changes": [{"path": "src/app.ts"}, {"path": ""}, {}],
            },
        },
        {
            "timestamp": "2026-01-01T00:00:02+00:00",
            "item": {"type": "agent_message", "text": "done"},
        },
        {"timestamp": "2026-01-01T00:00:03+00:00", "item": {"type": "agent_message"}},
        {"timestamp": "2026-01-01T00:00:04+00:00", "item": {"type": "other"}},
        {"timestamp": "2026-01-01T00:00:05+00:00", "item": None},
    ]
    monkeypatch.setattr(trace_events, "_read_jsonl_dicts", lambda _path: entries)
    monkeypatch.setattr(trace_events, "_extract_item_completed", lambda entry: entry["item"])

    events = trace_events.collect_trace_events(tmp_path, harness="codex-cli")

    assert [(event.event_type, event.data) for event in events] == [
        ("bash_command", {"command": "bun run build"}),
        ("gate_result", {"status": "failed", "exit_code": 0}),
        ("file_change", {"file_path": "src/app.ts"}),
        ("assistant_message", {"content": "done"}),
    ]
    assert trace_events.collect_trace_events(None, harness="codex-cli") == []
    assert trace_events.collect_trace_events(tmp_path, harness="gemini") == []


def test_gate_watcher_records_success_failures_repeats_and_termination(monkeypatch, tmp_path):
    outcomes = [
        subprocess.CompletedProcess(["build"], 0, stdout="ok", stderr=""),
        subprocess.CompletedProcess(["typecheck"], 1, stdout="", stderr="TS2345: bad type"),
        subprocess.CompletedProcess(["typecheck"], 1, stdout="", stderr="TS2345: again"),
    ]

    def fake_run(args, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return outcomes.pop(0)

    monkeypatch.setattr("raidar.watcher.gate_watcher.subprocess.run", fake_run)
    watcher = GateWatcher(max_failures=2)
    gates = [
        VerificationGate(name="build", command=["build"], on_failure="continue"),
        VerificationGate(name="typecheck-one", command=["typecheck"], on_failure="continue"),
        VerificationGate(name="typecheck-two", command=["typecheck"], on_failure="continue"),
        VerificationGate(name="not-run", command=["not-run"], on_failure="continue"),
    ]

    events = watcher.run_all_gates(gates, tmp_path)

    assert [event.exit_code for event in events] == [0, 1, 1]
    assert events[1].failure_category == "type_error"
    assert events[1].is_repeat is False
    assert events[2].is_repeat is True
    assert watcher.get_summary() == {
        "total_gates": 3,
        "passed": 1,
        "failed": 2,
        "unique_failure_categories": 1,
        "repeat_failures": 1,
        "terminated_early": True,
    }


def test_gate_watcher_records_timeout_missing_command_and_terminate_policy(monkeypatch, tmp_path):
    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["slow"], timeout=1)

    monkeypatch.setattr("raidar.watcher.gate_watcher.subprocess.run", timeout_run)
    timeout_event = GateWatcher().run_gate(
        VerificationGate(name="slow", command=["slow"], on_failure="continue"),
        tmp_path,
    )
    assert timeout_event.exit_code == -1
    assert timeout_event.stderr == "Command timed out"
    assert timeout_event.failure_category == "unknown"

    def missing_run(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("raidar.watcher.gate_watcher.subprocess.run", missing_run)
    watcher = GateWatcher(max_failures=10)
    events = watcher.run_all_gates(
        [
            VerificationGate(name="missing", command=["missing"], on_failure="terminate"),
            VerificationGate(name="not-run", command=["not-run"], on_failure="continue"),
        ],
        tmp_path,
    )
    assert len(events) == 1
    assert events[0].stderr == "Command not found: missing"


def test_failure_categorization_and_truncation_behaviour(monkeypatch):
    assert categorize_failure("", "") is None
    assert categorize_failure("plain failure", "") == "unknown"
    assert truncate_output("short", max_length=10) == "short"
    assert truncate_output("abcdefghijkl", max_length=4) == "abcd\n... (truncated, 12 total chars)"

    monkeypatch.setattr(
        "raidar.watcher.gate_watcher.settings",
        SimpleNamespace(gate=SimpleNamespace(max_output_length=3)),
    )
    assert truncate_output("abcdef") == "abc\n... (truncated, 6 total chars)"
