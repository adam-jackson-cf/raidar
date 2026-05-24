import json
import subprocess

import pytest

from raidar.runtime import harbor_preflight, harness_logs, scorecard_phase


def test_harness_event_stream_pointer_maps_supported_harnesses(tmp_path) -> None:
    assert (
        harness_logs._harness_event_stream_pointer(tmp_path, "codex-cli") == tmp_path / "codex.txt"
    )
    for harness in ("claude-code", "gemini", "cursor", "copilot", "pi"):
        assert (
            harness_logs._harness_event_stream_pointer(tmp_path, harness) == tmp_path / "commands"
        )
    with pytest.raises(ValueError, match="Unsupported harness"):
        harness_logs._harness_event_stream_pointer(tmp_path, "unknown")


def test_jsonl_dict_reader_ignores_missing_invalid_and_non_dict_entries(tmp_path) -> None:
    assert harness_logs._read_jsonl_dicts(tmp_path / "missing.jsonl") == []
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            [
                "",
                json.dumps({"type": "item.completed", "item": {"id": 1}}),
                "[1, 2]",
                "{not-json",
            ]
        ),
        encoding="utf-8",
    )

    entries = harness_logs._read_jsonl_dicts(path)

    assert entries == [{"type": "item.completed", "item": {"id": 1}}]
    assert harness_logs._extract_item_completed(entries[0]) == {"id": 1}
    assert harness_logs._extract_item_completed({"type": "item.completed", "item": "bad"}) is None
    assert harness_logs._extract_item_completed({"type": "other"}) is None
    assert harness_logs._as_int("7") == 7
    assert harness_logs._as_int("bad") is None


def test_docker_compose_preflight_reports_only_detected_unsupported_versions(monkeypatch) -> None:
    class Result:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self.stdout = stdout

    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return (
            Result(1, "") if cmd[-1] == "--short" else Result(0, "Docker Compose version v2.39.0")
        )

    monkeypatch.setattr(harbor_preflight.subprocess, "run", fake_run)

    reason = harbor_preflight._docker_compose_preflight_reason({"PATH": "/bin"})

    assert "Unsupported docker compose version 2.39.0" in reason
    assert calls == [["docker", "compose", "version", "--short"], ["docker", "compose", "version"]]

    monkeypatch.setattr(
        harbor_preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("docker", 15)),
    )
    assert harbor_preflight._docker_compose_preflight_reason({}) is None


def test_scorecard_phase_persists_verifier_artifacts_and_analysis(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    scorecard = object()
    phase = type("Phase", (), {"layout": object(), "context": object()})()
    execution = type("Execution", (), {"outputs": object(), "harbor_result": object()})()
    artifacts = object()

    monkeypatch.setattr(
        scorecard_phase,
        "build_scorecard",
        lambda context: calls.append(("build", context)) or scorecard,
    )
    monkeypatch.setattr(
        scorecard_phase,
        "persist_canonical_verifier_artifacts",
        lambda layout, built_scorecard, outputs: calls.append(
            ("persist", (layout, built_scorecard, outputs))
        ),
    )
    monkeypatch.setattr(
        scorecard_phase,
        "write_run_analysis",
        lambda layout, request, built_scorecard, harbor_result: calls.append(
            ("analysis", (layout, request, built_scorecard, harbor_result))
        ),
    )

    result = scorecard_phase.synthesize_scorecard_phase("request", phase, execution, artifacts)

    assert result is scorecard
    assert [name for name, _value in calls] == ["build", "persist", "analysis"]
