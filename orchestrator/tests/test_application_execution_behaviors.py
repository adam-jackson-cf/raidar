from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from raidar.application import execution
from raidar.application.models import (
    ExecutionDispatchRequest,
    ExperimentDispatchSettings,
    ExperimentRunRequest,
    RunCliOptions,
)


def _options(tmp_path: Path, *, repeats: int = 1) -> RunCliOptions:
    return RunCliOptions(
        scenario=tmp_path / "scenario.yaml",
        harness="codex-cli",
        provider="openai",
        model="gpt-5.5",
        timeout=300,
        repeats=repeats,
        repeat_parallel=1,
        rerun_unscored=0,
        experiments_root=tmp_path / "experiments",
        reasoning_effort="low",
    )


def _scenario() -> SimpleNamespace:
    return SimpleNamespace(
        name="Demo Scenario",
        scenario_revision="v001",
    )


def _run(tmp_path: Path, *, unscored: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id="run-1",
        duration_sec=12.5,
        terminated_early=False,
        termination_reason=None,
        scores=SimpleNamespace(
            unscored=unscored,
            unscored_reasons=["provider_rate_limit"] if unscored else [],
            execution_validity=SimpleNamespace(passed=True),
            performance_gates=SimpleNamespace(passed=True),
            composite_score=0.75,
            metadata={
                "run": {
                    "run_json_path": str(tmp_path / "runs" / "run-1" / "run.json"),
                    "canonical_run_dir": str(tmp_path / "runs" / "run-1"),
                }
            },
        ),
    )


def test_execute_run_command_returns_single_suite_without_forced_summary(
    monkeypatch, tmp_path, capsys
) -> None:
    scenario = _scenario()
    run = _run(tmp_path, unscored=True)
    request = SimpleNamespace(scenario=scenario)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        execution, "_load_project_env", lambda repo_root: captured.setdefault("env", repo_root)
    )
    monkeypatch.setattr(
        execution, "_cleanup_stale_harbor_before_runs", lambda: captured.setdefault("cleanup", True)
    )
    monkeypatch.setattr(
        execution,
        "prepared_run_request",
        lambda *_args, **_kwargs: (
            scenario,
            datetime(2026, 1, 1, tzinfo=UTC),
            tmp_path / "execution",
            request,
        ),
    )
    monkeypatch.setattr(execution, "execute_repeat_runs", lambda **_kwargs: ([run], 0, 0))
    monkeypatch.setattr(execution, "summary_result_path", lambda _run: tmp_path / "run.json")

    result = execution.execute_run_command(
        ExecutionDispatchRequest(
            options=_options(tmp_path),
            force_experiment_summary=False,
            cleanup_before_runs=True,
            echo=True,
        ),
        repo_root=tmp_path,
    )

    assert result.scenario_name == "Demo Scenario"
    assert result.runs == [run]
    assert result.summary_path is None
    assert captured == {"env": tmp_path, "cleanup": True}
    output = capsys.readouterr().out
    assert "Running scenario..." in output
    assert "Unscored reasons: ['provider_rate_limit']" in output


def test_execute_run_command_persists_experiment_summary(monkeypatch, tmp_path, capsys) -> None:
    scenario = _scenario()
    run = _run(tmp_path)
    request = SimpleNamespace(scenario=scenario)
    captured: dict[str, object] = {}

    monkeypatch.setattr(execution, "_load_project_env", lambda _repo_root: None)
    monkeypatch.setattr(execution, "_cleanup_stale_harbor_before_runs", lambda: None)
    monkeypatch.setattr(
        execution,
        "prepared_run_request",
        lambda *_args, **_kwargs: (
            scenario,
            datetime(2026, 1, 1, tzinfo=UTC),
            tmp_path / "execution",
            request,
        ),
    )
    monkeypatch.setattr(execution, "execute_repeat_runs", lambda **_kwargs: ([run], 1, 0))
    monkeypatch.setattr(execution, "scenario_evaluation_profile", lambda _scenario: "code-task")
    monkeypatch.setattr(execution, "scenario_metrics", lambda _scenario: ["functional"])
    monkeypatch.setattr(execution, "scenario_scorers", lambda _scenario: ("typescript-code-task",))

    def create_summary(summary_input):
        captured["summary_input"] = summary_input
        return {"summary": True}

    monkeypatch.setattr(execution, "create_experiment_summary", create_summary)
    monkeypatch.setattr(
        execution,
        "persist_experiment",
        lambda _execution_dir, _summary: (
            tmp_path / "experiment.json",
            tmp_path / "summary.json",
            tmp_path / "report.md",
        ),
    )

    result = execution.execute_run_command(
        ExecutionDispatchRequest(
            options=_options(tmp_path, repeats=2),
            force_experiment_summary=True,
            cleanup_before_runs=False,
            echo=True,
        ),
        repo_root=tmp_path,
    )

    summary_input = captured["summary_input"]
    assert summary_input.evaluation_profile == "code-task"
    assert summary_input.scorers == ["typescript-code-task"]
    assert summary_input.repeat_parallel == 1
    assert summary_input.reruns_used == 1
    assert result.experiment_json_path == tmp_path / "experiment.json"
    assert "Experiment record:" in capsys.readouterr().out


def test_dispatch_from_experiment_request_sets_default_summary_suffix(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}

    def fake_execute(dispatch_request, *, repo_root):
        captured["repo_root"] = repo_root
        captured["request"] = dispatch_request
        return SimpleNamespace(summary_path=tmp_path / "summary.json")

    monkeypatch.setattr(execution, "execute_run_command", fake_execute)

    result = execution.dispatch_from_experiment_request(
        ExperimentRunRequest(
            scenario=tmp_path / "scenario.yaml",
            harness="codex-cli",
            provider="openai",
            model="gpt-5.5",
            timeout=300,
            repeats=2,
            repeat_parallel=1,
            rerun_unscored=1,
            experiment_kind="benchmark",
            experiments_root=None,
            reasoning_effort="medium",
        ),
        ExperimentDispatchSettings(repo_root=tmp_path, force_experiment_summary=True),
    )

    dispatch_request = captured["request"]
    assert result.summary_path == tmp_path / "summary.json"
    assert captured["repo_root"] == tmp_path
    assert dispatch_request.execution_suffix == "codex-cli__openai-gpt-5.5__medium"
    assert dispatch_request.options.experiments_root == tmp_path / "experiments" / "benchmarks"
