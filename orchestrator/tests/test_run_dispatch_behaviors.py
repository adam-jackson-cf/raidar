from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from click import ClickException

from raidar.agents.config import Harness
from raidar.application import run_dispatch
from raidar.application.models import RunCliOptions
from raidar.runtime.starter_preflight import StarterPreflightError
from raidar.schemas.events import GateEvent
from raidar.schemas.scorecard import EvalConfig, EvalRun, MetricScore, Scorecard


def _run(run_id: str, *, unscored: bool = False, root: Path | None = None):
    run_json_path = (root or Path("/tmp")) / run_id / "run.json"
    return SimpleNamespace(
        id=run_id,
        scores=SimpleNamespace(
            unscored=unscored,
            metadata={"run": {"run_json_path": str(run_json_path)}},
        ),
        model_dump_json=lambda indent=2: f'{{"id":"{run_id}"}}',
    )


def _request(tmp_path):
    return SimpleNamespace(
        scenario=SimpleNamespace(name="Demo Task", scenario_revision="v001"),
        config=SimpleNamespace(),
        scenario_dir=tmp_path / "scenario",
        execution_dir=tmp_path / "exec",
        repeat_index=1,
    )


def _options(tmp_path, *, reasoning_effort="low"):
    return RunCliOptions(
        scenario=tmp_path / "scenario" / "scenario.yaml",
        harness="codex-cli",
        provider="openai",
        model="gpt-5.5",
        timeout=123,
        repeats=2,
        repeat_parallel=1,
        rerun_unscored=1,
        experiments_root=tmp_path / "experiments",
        reasoning_effort=reasoning_effort,
    )


def test_summary_and_persist_eval_run_require_canonical_path(tmp_path):
    run = _run("run-a", root=tmp_path)

    path = run_dispatch.persist_eval_run(run)

    assert path == tmp_path / "run-a" / "run.json"

    missing = _run("missing")
    missing.scores.metadata["run"] = {}
    with pytest.raises(ClickException, match="Canonical run.json path missing"):
        run_dispatch.summary_result_path(missing)


def test_persist_eval_run_redacts_secret_shaped_runtime_evidence(tmp_path):
    run_json_path = tmp_path / "run" / "run.json"
    run = EvalRun(
        id="run",
        timestamp=datetime.now(UTC).isoformat(),
        config=EvalConfig(
            model="model",
            harness="codex-cli",
            scenario_name="scenario",
            scenario_revision="v001",
            starter_root="starter",
            evaluation_profile="functional",
        ),
        duration_sec=1.0,
        termination_reason="OPENAI_API_KEY=abcdefghijklmnop",
        scores=Scorecard(
            metadata={"run": {"run_json_path": str(run_json_path)}},
            metric_scores=[
                MetricScore(
                    metric_id="functional",
                    score=1.0,
                    passed=True,
                    evidence="Bearer abcdefghijklmnop",
                    judge_output={"raw": "ANTHROPIC_API_KEY=abcdefghijklmnop"},
                )
            ],
        ),
        gate_history=[
            GateEvent(
                timestamp="2026-01-01T00:00:00+00:00",
                gate_name="test",
                command="pytest",
                exit_code=0,
                stdout="OPENAI_API_KEY=abcdefghijklmnop",
                stderr="password=hunter2value",
            )
        ],
    )

    path = run_dispatch.persist_eval_run(run)

    text = path.read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in text
    assert "hunter2value" not in text
    assert "OPENAI_API_KEY=<redacted>" in text
    assert "ANTHROPIC_API_KEY=<redacted>" in text


def test_prepared_request_builds_execution_dir_and_agent_spec(monkeypatch, tmp_path):
    scenario = SimpleNamespace(name="Demo Task", scenario_revision="v001")
    scenario.starter = SimpleNamespace(root="starter")
    monkeypatch.setattr(run_dispatch, "load_scenario", lambda _path: scenario)
    monkeypatch.setattr(
        run_dispatch,
        "_execution_id",
        lambda name, revision, started_at, execution_suffix: "exec-id",
    )

    scenario_def, started_at, execution_dir, request = run_dispatch.prepared_run_request(
        _options(tmp_path),
        execution_suffix="suffix",
    )

    assert scenario_def is scenario
    assert started_at.tzinfo == UTC
    assert execution_dir == tmp_path / "experiments" / "exec-id"
    assert request.config.harness == Harness.CODEX_CLI
    assert request.config.model.reasoning_effort == "low"
    assert request.scenario_dir == tmp_path / "scenario"


def test_execute_repeat_index_wraps_runtime_errors_but_not_starter_preflight(monkeypatch, tmp_path):
    request = _request(tmp_path)
    calls: list[int] = []

    def fake_execute(run_request):
        calls.append(run_request.repeat_index)
        return _run(f"run-{run_request.repeat_index}")

    monkeypatch.setattr(run_dispatch, "_execute_run_request", fake_execute)
    assert run_dispatch._execute_repeat_index(request, 3).id == "run-3"
    assert calls == [3]

    monkeypatch.setattr(
        run_dispatch,
        "_execute_run_request",
        lambda _request: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(ClickException, match="Repeat 2 failed: boom"):
        run_dispatch._execute_repeat_index(request, 2)

    monkeypatch.setattr(
        run_dispatch,
        "_execute_run_request",
        lambda _request: (_ for _ in ()).throw(StarterPreflightError("fatal")),
    )
    with pytest.raises(StarterPreflightError):
        run_dispatch._execute_repeat_index(request, 2)


def test_repeat_batches_run_empty_sequential_and_parallel(monkeypatch, tmp_path):
    request = _request(tmp_path)
    monkeypatch.setattr(
        run_dispatch,
        "_execute_repeat_index",
        lambda _request, repeat_index: _run(f"run-{repeat_index}"),
    )

    assert (
        run_dispatch._execute_repeat_batch(
            request=request,
            batch_size=0,
            repeat_parallel=1,
            start_index=1,
        )
        == []
    )
    assert [
        run.id
        for run in run_dispatch._execute_repeat_batch(
            request=request,
            batch_size=2,
            repeat_parallel=1,
            start_index=1,
        )
    ] == ["run-1", "run-2"]
    assert [
        run.id
        for run in run_dispatch._execute_repeat_batch(
            request=request,
            batch_size=3,
            repeat_parallel=5,
            start_index=4,
        )
    ] == ["run-4", "run-5", "run-6"]


def test_unscored_reruns_retry_once_and_reports_pending(monkeypatch, tmp_path):
    request = _request(tmp_path)
    batches = [
        [_run("run-1", unscored=True), _run("run-2", unscored=False)],
        [_run("run-3", unscored=True)],
    ]

    monkeypatch.setattr(
        run_dispatch,
        "_execute_repeat_batch",
        lambda **_kwargs: batches.pop(0),
    )

    runs, retries_used, pending = run_dispatch._run_with_unscored_reruns(
        request=request,
        repeats=2,
        repeat_parallel=1,
        rerun_unscored=1,
    )

    assert [run.id for run in runs] == ["run-1", "run-2", "run-3"]
    assert retries_used == 1
    assert pending == 1


def test_unscored_reruns_abort_on_starter_preflight(monkeypatch, tmp_path):
    request = _request(tmp_path)
    monkeypatch.setattr(
        run_dispatch,
        "_execute_repeat_batch",
        lambda **_kwargs: (_ for _ in ()).throw(StarterPreflightError("fatal")),
    )

    with pytest.raises(ClickException, match="Fatal starter preflight error"):
        run_dispatch._run_with_unscored_reruns(
            request=request,
            repeats=1,
            repeat_parallel=1,
            rerun_unscored=1,
        )


def test_execute_repeat_runs_wraps_errors_and_execution_id_suffix(monkeypatch, tmp_path):
    request = _request(tmp_path)
    monkeypatch.setattr(
        run_dispatch,
        "_run_with_unscored_reruns",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(ClickException, match="boom"):
        run_dispatch.execute_repeat_runs(
            request=request,
            repeats=0,
            repeat_parallel=1,
            rerun_unscored=0,
        )

    started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert run_dispatch._execution_id("Demo Task", "v001", started_at) == (
        "20260102-030405Z__demo-task__v001"
    )
    assert run_dispatch._execution_id("Demo Task", "v001", started_at, "codex") == (
        "20260102-030405Z__demo-task__v001__codex"
    )
