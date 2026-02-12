"""Tests for Harbor runtime env and stale build cleanup behavior."""

import signal
import subprocess
from pathlib import Path

import pytest

import agentic_eval.runner as runner


class _AdapterStub:
    def runtime_env(self) -> dict[str, str]:
        return {"ADAPTER_FLAG": "1", "COMPOSE_BAKE": "1"}


def test_build_harbor_run_env_forces_compose_bake_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(_AdapterStub())

    assert env["ADAPTER_FLAG"] == "1"
    assert env["COMPOSE_BAKE"] == "false"
    assert "OPENAI_API_KEY" not in env
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" in env
    secret_file = runner.Path(env["AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY"])
    assert secret_file.exists()
    assert secret_file.read_text(encoding="utf-8") == "test-openai-key"
    assert "DOCKER_CONFIG" not in env


class _ExecAdapterStub:
    def build_harbor_command(self, *, task_path: Path, job_name: str, jobs_dir: Path) -> list[str]:
        del task_path, job_name, jobs_dir
        return ["harbor", "run"]


def test_cleanup_stale_harbor_build_processes_only_kills_orphans(
    monkeypatch,
) -> None:
    ps_output = "\n".join(
        [
            "1001 1 docker compose -p harbor-task-a -f /tmp/docker-compose-build.yaml build",
            "1002 42 docker compose -p harbor-task-b -f /tmp/docker-compose-build.yaml build",
            (
                "1003 1 /Users/me/.docker/cli-plugins/docker-compose compose "
                "-p harbor-task-c -f /tmp/docker-compose-build.yaml build"
            ),
            (
                "1004 99 /Users/me/.docker/cli-plugins/docker-buildx bake --file - "
                "--progress rawjson --metadata-file /tmp/meta "
                "--allow fs.read=/tmp/harbor-task-one/environment"
            ),
            (
                "1005 1 /Users/me/.docker/cli-plugins/docker-buildx bake --file - "
                "--progress rawjson --metadata-file /tmp/meta "
                "--allow fs.read=/tmp/harbor-task-two/environment"
            ),
            "1006 1 sleep 30",
        ]
    )

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["ps", "-ax", "-o", "pid=,ppid=,command="], 0, stdout=ps_output, stderr=""
        )

    killed: list[tuple[int, signal.Signals]] = []

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.os, "kill", fake_kill)

    runner.cleanup_stale_harbor_build_processes()

    assert killed == [
        (1001, signal.SIGTERM),
        (1003, signal.SIGTERM),
        (1005, signal.SIGTERM),
    ]


def test_cleanup_stale_harbor_build_processes_kills_orphan_harbor_run_trees(
    monkeypatch,
) -> None:
    ps_output = "\n".join(
        [
            "2001 1 /Users/me/.local/bin/harbor run --path /tmp/harbor-task-abc --job-name x",
            (
                "2002 2001 /Users/me/.docker/cli-plugins/docker-compose compose "
                "-p harbor-task-abc -f /tmp/docker-compose-build.yaml build"
            ),
            (
                "2003 2002 /Users/me/.docker/cli-plugins/docker-buildx bake --file - "
                "--progress rawjson --metadata-file /tmp/meta "
                "--allow fs.read=/tmp/harbor-task-abc/environment"
            ),
            "2004 42 /Users/me/.local/bin/harbor run --path /tmp/harbor-task-def --job-name y",
        ]
    )

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            0,
            stdout=ps_output,
            stderr="",
        )

    killed: list[tuple[int, signal.Signals]] = []

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.os, "kill", fake_kill)

    runner.cleanup_stale_harbor_build_processes()

    assert killed == [
        (2001, signal.SIGTERM),
        (2002, signal.SIGTERM),
        (2003, signal.SIGTERM),
    ]


def test_parse_docker_compose_version_variants() -> None:
    assert runner._parse_docker_compose_version("2.40.1") == (2, 40, 1)
    assert runner._parse_docker_compose_version("v2.40.1-desktop.1") == (2, 40, 1)
    assert runner._parse_docker_compose_version("Docker Compose version v2.39.2") == (2, 39, 2)
    assert runner._parse_docker_compose_version("unknown") is None


def test_docker_compose_preflight_reason_flags_old_versions(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["docker", "compose", "version", "--short"],
            0,
            stdout="2.39.2\n",
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    reason = runner._docker_compose_preflight_reason({})

    assert reason == "Unsupported docker compose version 2.39.2. Require >= 2.40.1 for Harbor runs."


def test_docker_compose_preflight_reason_allows_supported_versions(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            ["docker", "compose", "version", "--short"],
            0,
            stdout="2.40.1\n",
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    assert runner._docker_compose_preflight_reason({}) is None


def test_redact_sensitive_text_masks_inline_env_and_json_values() -> None:
    original = (
        "docker compose exec -e ANTHROPIC_API_KEY=sk-ant-secret "
        '-e OPENAI_API_KEY=sk-openai-secret payload={"GEMINI_API_KEY":"abc123"}'
    )

    redacted = runner._redact_sensitive_text(original)

    assert "sk-ant-secret" not in redacted
    assert "sk-openai-secret" not in redacted
    assert '"GEMINI_API_KEY":"abc123"' not in redacted
    assert "ANTHROPIC_API_KEY=[REDACTED]" in redacted
    assert "OPENAI_API_KEY=[REDACTED]" in redacted
    assert '"GEMINI_API_KEY":"[REDACTED]"' in redacted


def test_validate_public_base_images_rejects_private_registry() -> None:
    with pytest.raises(ValueError, match="private or unsupported registry host"):
        runner._validate_public_base_images("FROM registry.company.com/platform/base:1\n")


def test_validate_public_base_images_accepts_public_images() -> None:
    runner._validate_public_base_images("FROM oven/bun:1\nFROM ghcr.io/acme/tooling:latest\n")


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

    def fake_run_harbor_process(**kwargs):
        del kwargs
        attempts.append(1)
        return "Harbor exited with code 1" if len(attempts) == 1 else None

    sleeps: list[int] = []

    monkeypatch.setattr(runner, "_run_harbor_process", fake_run_harbor_process)
    monkeypatch.setattr(runner, "_is_registry_rate_limited", lambda _: True)
    monkeypatch.setattr(runner, "cleanup_stale_harbor_resources", lambda: None)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: sleeps.append(seconds))
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

    def fake_run_harbor_process(**kwargs):
        del kwargs
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
