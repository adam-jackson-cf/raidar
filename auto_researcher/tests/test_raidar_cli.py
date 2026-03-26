"""Tests for the in-process Raidar service client."""

from __future__ import annotations

from pathlib import Path

from raidar.application.models import ExperimentRunRequest, ScenarioInitRequest

from auto_researcher.raidar_cli import RaidarServiceClient
from auto_researcher.storage import WorkspaceLayout


def test_service_client_initializes_scenario_in_process(tmp_path: Path) -> None:
    client = RaidarServiceClient(layout=WorkspaceLayout(tmp_path))

    result = client.scenario_init(
        ScenarioInitRequest(
            path=tmp_path / "scenarios" / "sample-task",
            name="Sample Task",
            scenario_revision="v001",
            starter_root="starter",
            prompt_entry="prompt/task.md",
            difficulty="medium",
            category="greenfield-ui",
            timeout_sec=600,
        )
    )

    assert result.scenario_yaml.is_file()
    assert result.prompt_path.is_file()
    assert result.rules_dir.is_dir()


def test_service_client_runs_experiment_in_process(monkeypatch, tmp_path: Path) -> None:
    client = RaidarServiceClient(layout=WorkspaceLayout(tmp_path))
    captured: dict[str, object] = {}

    def fake_dispatch(request, *, repo_root):
        captured["request"] = request
        captured["repo_root"] = repo_root
        return "result"

    monkeypatch.setattr(
        "auto_researcher.raidar_cli.dispatch_from_experiment_request",
        fake_dispatch,
    )

    result = client.experiment_run(
        ExperimentRunRequest(
            scenario=tmp_path / "scenarios" / "sample-task" / "v001" / "scenario.yaml",
            harness="codex-cli",
            model="codex/gpt-5.4-mini",
            timeout=300,
            repeats=3,
            repeat_parallel=1,
            rerun_unscored=1,
            experiment_kind="benchmark",
        )
    )

    assert result == "result"
    assert captured["repo_root"] == tmp_path
