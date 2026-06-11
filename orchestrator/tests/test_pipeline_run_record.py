"""Regression coverage for the run-record assembly in the runtime pipeline."""

from datetime import UTC, datetime
from types import SimpleNamespace

from raidar.runtime import pipeline
from raidar.schemas.events import GateEvent, TraceEvent
from raidar.schemas.scorecard import Scorecard


def test_run_task_persists_execution_trace_events(monkeypatch):
    trace_event = TraceEvent(
        timestamp="2026-01-01T00:00:00+00:00",
        event_type="bash_command",
        data={"command": "bun run test"},
    )
    gate_event = GateEvent(
        timestamp="2026-01-01T00:00:01+00:00",
        gate_name="test",
        command="bun run test",
        exit_code=0,
        stdout="",
        stderr="",
    )
    prepared = SimpleNamespace(
        layout=SimpleNamespace(run_id="run-1", start_time=datetime(2026, 1, 1, tzinfo=UTC))
    )
    execution = SimpleNamespace(
        duration_sec=1.0,
        terminated_early=False,
        termination_reason=None,
        events=[trace_event],
        outputs=SimpleNamespace(gate_history=[gate_event]),
    )
    request = SimpleNamespace(
        config=SimpleNamespace(
            model=SimpleNamespace(qualified_name="openai/gpt-5.5"),
            harness=SimpleNamespace(value="codex-cli"),
        ),
        scenario=SimpleNamespace(
            name="demo",
            scenario_revision="v001",
            starter=SimpleNamespace(root="starter"),
        ),
    )
    monkeypatch.setattr(pipeline, "prepare_workspace_phase", lambda _request: prepared)
    monkeypatch.setattr(pipeline, "execute_harbor_phase", lambda _request, _prepared: execution)
    monkeypatch.setattr(pipeline, "persist_artifacts_phase", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        pipeline, "synthesize_scorecard_phase", lambda *_args: Scorecard(run_id="run-1")
    )
    monkeypatch.setattr(pipeline, "scenario_evaluation_profile", lambda _scenario: "profile")
    monkeypatch.setattr(pipeline, "scenario_scorers", lambda _scenario: ["bugfix@1"])

    run = pipeline.run_task(request)

    assert run.traces == [trace_event]
    assert run.gate_history == [gate_event]
