"""Focused Harbor runtime tests."""

# ruff: noqa: F403, F405
from tests.harbor_runtime_support import *  # noqa: F403


def test_execute_harbor_retries_once_on_registry_rate_limit(monkeypatch, tmp_path) -> None:
    request = runner.HarborExecutionRequest(
        adapter=_ExecAdapterStub(),
        workspace=tmp_path / "workspace",
        task_bundle_path=tmp_path / "task-bundle",
        jobs_dir=tmp_path / "jobs",
        run_harbor_dir=tmp_path / "harbor",
        run_id="abc12345",
        timeout_sec=60,
        run_env={},
    )
    request.workspace.mkdir(parents=True, exist_ok=True)
    request.run_harbor_dir.mkdir(parents=True, exist_ok=True)
    request.jobs_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[int] = []

    def fake_run_harbor_process(process_request):
        assert process_request.workspace == request.workspace
        assert process_request.run_harbor_dir == request.run_harbor_dir
        attempts.append(1)
        return "Harbor exited with code 1" if len(attempts) == 1 else None

    sleeps: list[int] = []

    monkeypatch.setattr(runner, "_run_harbor_process", fake_run_harbor_process)
    monkeypatch.setattr(runner, "_is_registry_rate_limited", lambda _: True)
    monkeypatch.setattr(runner, "cleanup_stale_harbor_resources", lambda: None)
    monkeypatch.setattr(
        runner,
        "wait_for_harbor_rate_limit_retry",
        lambda: sleeps.append(runner.HARBOR_RATE_LIMIT_RETRY_DELAY_SEC),
    )
    monkeypatch.setattr(runner, "detect_trial_failure", lambda _: None)

    result = runner.execute_harbor(request)

    assert result.terminated_early is False
    assert len(attempts) == 2
    assert sleeps == [runner.HARBOR_RATE_LIMIT_RETRY_DELAY_SEC]


def test_execute_harbor_does_not_retry_non_rate_limit(monkeypatch, tmp_path) -> None:
    request = runner.HarborExecutionRequest(
        adapter=_ExecAdapterStub(),
        workspace=tmp_path / "workspace",
        task_bundle_path=tmp_path / "task-bundle",
        jobs_dir=tmp_path / "jobs",
        run_harbor_dir=tmp_path / "harbor",
        run_id="abc12345",
        timeout_sec=60,
        run_env={},
    )
    request.workspace.mkdir(parents=True, exist_ok=True)
    request.run_harbor_dir.mkdir(parents=True, exist_ok=True)
    request.jobs_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[int] = []

    def fake_run_harbor_process(process_request):
        assert process_request.workspace == request.workspace
        assert process_request.run_harbor_dir == request.run_harbor_dir
        attempts.append(1)
        return "Harbor exited with code 1"

    sleeps: list[int] = []

    monkeypatch.setattr(runner, "_run_harbor_process", fake_run_harbor_process)
    monkeypatch.setattr(runner, "_is_registry_rate_limited", lambda _: False)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = runner.execute_harbor(request)

    assert result.terminated_early is True
    assert result.termination_reason == "Harbor exited with code 1"
    assert len(attempts) == 1
    assert sleeps == []
