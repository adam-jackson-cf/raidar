import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

from raidar.runtime import harbor_execution, harbor_results, maintenance, pipeline
from raidar.runtime.models import HarborExecutionRequest
from raidar.schemas.scorecard import Scorecard


def test_harbor_result_parsing_detects_rate_limits_failures_and_timings(tmp_path):
    harbor_dir = tmp_path / "harbor"
    harbor_dir.mkdir()
    (harbor_dir / "harbor-stdout.log").write_text("429 pull rate limit exceeded", encoding="utf-8")
    assert harbor_results._is_registry_rate_limited(harbor_dir) is True
    (harbor_dir / "harbor-stdout.log").write_text("ok", encoding="utf-8")
    assert harbor_results._is_registry_rate_limited(harbor_dir) is False

    trial = tmp_path / "trial"
    trial.mkdir()
    assert harbor_results.detect_trial_failure(None) is None
    assert harbor_results.detect_trial_failure(trial) is None

    (trial / "result.json").write_text(
        json.dumps({"exception_info": {"exception_message": "failed with sk-test-secret"}}),
        encoding="utf-8",
    )
    failure = harbor_results.detect_trial_failure(trial)
    assert failure == harbor_results.TrialFailure(
        reason="Harbor trial exception: failed with sk-test-secret",
        code="harbor_trial_exception",
    )

    (trial / "result.json").write_text("{}", encoding="utf-8")
    agent = trial / "agent"
    agent.mkdir()
    (agent / "codex.txt").write_text(
        '{"type":"turn.failed","error":{"message":"bad token"}}\n',
        encoding="utf-8",
    )
    assert harbor_results.detect_trial_failure(trial) == harbor_results.TrialFailure(
        reason="Codex turn failed: bad token",
        code="provider_or_harness_turn_failure",
    )
    assert harbor_results._codex_turn_failure_message('{"type":"turn.failed","error":{}}') is None
    assert harbor_results._codex_turn_failure_message("raw failure") == "raw failure"

    result_payload = {
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:03Z",
        "environment_setup": {
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
        },
        "agent_setup": {},
        "agent_execution": "bad",
        "verifier": {
            "started_at": "2026-01-01T00:00:02+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
        },
    }
    (trial / "result.json").write_text(json.dumps(result_payload), encoding="utf-8")
    assert harbor_results._harbor_phase_timings(trial) == {
        "trial_total_sec": 3.0,
        "environment_setup_sec": 1.0,
        "verifier_sec": 0.0,
    }
    assert harbor_results._harbor_phase_timings(None) == {}
    assert harbor_results._parse_iso8601_timestamp("not-a-date") is None
    assert harbor_results._load_json_dict(trial / "missing.json") == {}
    (trial / "bad.json").write_text("[]", encoding="utf-8")
    assert harbor_results._load_json_dict(trial / "bad.json") == {}


def test_harbor_process_request_retry_and_timeout_behaviour(monkeypatch, tmp_path):
    request = harbor_execution.HarborProcessRequest(
        harbor_cmd=["harbor", "run"],
        workspace=tmp_path,
        timeout_sec=5,
        run_env={},
        run_harbor_dir=tmp_path / "harbor",
        job_dir=tmp_path / "job",
    )
    calls = {"count": 0}
    cleanup_calls: list[str] = []
    wait_calls: list[str] = []

    def fake_run_process(_request):
        calls["count"] += 1
        return (
            harbor_execution.HarborProcessFailure("Harbor exited with code 1", "harbor_cli_failure")
            if calls["count"] == 1
            else None
        )

    monkeypatch.setattr(harbor_execution, "_run_harbor_process", fake_run_process)
    monkeypatch.setattr(harbor_execution, "_is_registry_rate_limited", lambda _dir: True)
    monkeypatch.setattr(
        harbor_execution, "cleanup_stale_harbor_resources", lambda: cleanup_calls.append("cleanup")
    )
    monkeypatch.setattr(
        harbor_execution, "wait_for_harbor_rate_limit_retry", lambda: wait_calls.append("wait")
    )

    assert harbor_execution._run_harbor_with_retries(request) is None
    assert cleanup_calls == ["cleanup"]
    assert wait_calls == ["wait"]

    assert (
        harbor_execution._should_retry_harbor_rate_limit(
            attempt=2,
            execution_error=harbor_execution.HarborProcessFailure(
                "Harbor exited with code 1",
                "harbor_cli_failure",
            ),
            run_harbor_dir=tmp_path,
        )
        is False
    )
    assert harbor_execution._harbor_process_timeout(100) == 220
    assert harbor_execution._harbor_process_timeout(1000) == 1250

    assert harbor_execution._timeout_reason(timeout_sec=5, job_dir=tmp_path / "missing").endswith(
        "before creating a job directory."
    )
    job = tmp_path / "job"
    job.mkdir()
    assert harbor_execution._timeout_reason(timeout_sec=5, job_dir=job).endswith(
        "before creating a trial directory."
    )
    trial = job / "trial"
    trial.mkdir()
    assert harbor_execution._timeout_reason(timeout_sec=5, job_dir=job).endswith(
        "before trial result.json was written."
    )
    (trial / "result.json").write_text("{}", encoding="utf-8")
    assert (
        harbor_execution._timeout_reason(timeout_sec=5, job_dir=job) == "Harbor timed out after 5s."
    )


def test_run_harbor_process_writes_redacted_logs_for_success_failure_and_timeout(
    monkeypatch, tmp_path
):
    request = harbor_execution.HarborProcessRequest(
        harbor_cmd=["harbor", "run", "--token", "sk-secret-value"],
        workspace=tmp_path,
        timeout_sec=5,
        run_env={},
        run_harbor_dir=tmp_path / "harbor",
        job_dir=tmp_path / "job",
    )
    monkeypatch.setattr(harbor_execution, "_docker_compose_preflight_reason", lambda _env: None)

    class _Process:
        pid = 123
        returncode = 0

        def communicate(self, timeout=None):
            return ("stdout sk-secret-value", "stderr")

    monkeypatch.setattr(harbor_execution.subprocess, "Popen", lambda *_args, **_kwargs: _Process())
    assert harbor_execution._run_harbor_process(request) is None
    assert (request.run_harbor_dir / "harbor-stdout.log").read_text() == "stdout sk-secret-value"

    class _FailingProcess(_Process):
        returncode = 7

    monkeypatch.setattr(
        harbor_execution.subprocess, "Popen", lambda *_args, **_kwargs: _FailingProcess()
    )
    failure = harbor_execution._run_harbor_process(request)
    assert failure == harbor_execution.HarborProcessFailure(
        "Harbor exited with code 7",
        "harbor_cli_failure",
    )

    class _TimeoutProcess(_Process):
        returncode = 0
        attempts = 0

        def communicate(self, timeout=None):
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired(["harbor"], timeout=timeout)
            return ("late", "")

    monkeypatch.setattr(
        harbor_execution.subprocess, "Popen", lambda *_args, **_kwargs: _TimeoutProcess()
    )
    monkeypatch.setattr(harbor_execution, "_terminate_process_group", lambda _process: None)
    failure = harbor_execution._run_harbor_process(request)
    assert failure is not None
    assert failure.code == "harbor_timeout"
    assert "Harbor timed out after 5s" in failure.reason

    monkeypatch.setattr(
        harbor_execution,
        "_docker_compose_preflight_reason",
        lambda _env: "compose unavailable",
    )
    assert harbor_execution._run_harbor_process(request) == harbor_execution.HarborProcessFailure(
        "compose unavailable",
        "compose_version_unsupported",
    )

    monkeypatch.setattr(harbor_execution, "_docker_compose_preflight_reason", lambda _env: None)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(harbor_execution.subprocess, "Popen", missing)
    assert harbor_execution._run_harbor_process(request) == harbor_execution.HarborProcessFailure(
        "Harbor not installed",
        "harness_unavailable",
    )


def test_terminate_process_group_handles_running_exited_and_missing_processes(monkeypatch):
    signals: list[int] = []

    class _AlreadyExited:
        pid = 1

        def poll(self):
            return 0

    harbor_execution._terminate_process_group(_AlreadyExited())
    assert signals == []

    class _Running:
        pid = 2
        waits = 0

        def poll(self):
            return None

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired(["harbor"], timeout=timeout)

    monkeypatch.setattr(harbor_execution.os, "killpg", lambda pid, sig: signals.append(sig))
    harbor_execution._terminate_process_group(_Running())
    assert signals == [harbor_execution.signal.SIGTERM, harbor_execution.signal.SIGKILL]

    monkeypatch.setattr(
        harbor_execution.os,
        "killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    harbor_execution._terminate_process_group(_Running())


def test_execute_harbor_selects_trials_and_reports_execution_or_trial_failures(
    monkeypatch, tmp_path
):
    class _Adapter:
        def build_harbor_command(self, *, task_path, job_name, jobs_dir):
            return ["harbor", str(task_path), job_name, str(jobs_dir)]

    request = HarborExecutionRequest(
        adapter=_Adapter(),
        workspace=tmp_path,
        task_bundle_path=tmp_path / "task",
        jobs_dir=tmp_path / "jobs",
        run_harbor_dir=tmp_path / "harbor",
        run_id="run",
        timeout_sec=5,
        run_env={},
    )
    monkeypatch.setattr(
        harbor_execution,
        "_run_harbor_with_retries",
        lambda _request: harbor_execution.HarborProcessFailure("bad", "harbor_cli_failure"),
    )
    result = harbor_execution.execute_harbor(request)
    assert result.terminated_early is True
    assert result.termination_reason == "bad"
    assert result.failure_code == "harbor_cli_failure"

    job_dir = request.jobs_dir / "orchestrator-run"
    (job_dir / "trial-a").mkdir(parents=True)
    (job_dir / "trial-b" / "agent").mkdir(parents=True)
    assert harbor_execution._select_trial_dir(job_dir).name == "trial-b"

    monkeypatch.setattr(harbor_execution, "_run_harbor_with_retries", lambda _request: None)
    monkeypatch.setattr(
        harbor_execution,
        "detect_trial_failure",
        lambda _trial: harbor_results.TrialFailure("trial failed", "harbor_trial_exception"),
    )
    result = harbor_execution.execute_harbor(request)
    assert result.terminated_early is True
    assert result.trial_dir.name == "trial-b"
    assert result.failure_code == "harbor_trial_exception"

    monkeypatch.setattr(harbor_execution, "detect_trial_failure", lambda _trial: None)
    result = harbor_execution.execute_harbor(request)
    assert result.terminated_early is False
    assert result.trial_dir.name == "trial-b"


def test_pipeline_and_maintenance_delegate_to_runtime_phases(monkeypatch, tmp_path):
    prepared = SimpleNamespace(
        layout=SimpleNamespace(
            run_id="run",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    execution = SimpleNamespace(
        duration_sec=1.5,
        terminated_early=False,
        termination_reason=None,
        events=[],
        outputs=SimpleNamespace(gate_history=[]),
    )
    request = SimpleNamespace(
        config=SimpleNamespace(
            model=SimpleNamespace(qualified_name="openai/gpt"),
            harness=SimpleNamespace(value="codex-cli"),
        ),
        scenario=SimpleNamespace(
            name="scenario",
            scenario_revision="v001",
            starter=SimpleNamespace(root="starter"),
        ),
    )
    monkeypatch.setattr(pipeline, "prepare_workspace_phase", lambda _request: prepared)
    monkeypatch.setattr(pipeline, "execute_harbor_phase", lambda _request, _prepared: execution)
    monkeypatch.setattr(pipeline, "persist_artifacts_phase", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(pipeline, "synthesize_scorecard_phase", lambda *_args: Scorecard())
    monkeypatch.setattr(pipeline, "scenario_evaluation_profile", lambda _scenario: "profile")
    monkeypatch.setattr(pipeline, "scenario_scorers", lambda _scenario: ["resource-efficiency"])

    run = pipeline.run_task(request)

    assert run.id == "run"
    assert run.config.scorers == ["resource-efficiency"]
    assert run.duration_sec == 1.5

    cleanup_calls: list[tuple[bool, bool]] = []
    preflight_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        "raidar.runtime.harbor_cleanup.cleanup_stale_harbor_resources",
        lambda **kwargs: cleanup_calls.append(
            (kwargs["include_containers"], kwargs["include_build_processes"])
        ),
    )
    monkeypatch.setattr(
        "raidar.runtime.harbor_preflight._docker_compose_preflight_reason",
        lambda env: preflight_calls.append(env) or "reason",
    )
    maintenance.cleanup_stale_harbor_resources(
        include_containers=False, include_build_processes=True
    )
    assert cleanup_calls == [(False, True)]
    assert maintenance.docker_compose_preflight_reason({"A": "1"}) == "reason"
    assert preflight_calls == [{"A": "1"}]
