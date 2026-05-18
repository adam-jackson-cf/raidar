"""Tests for Harbor runtime env and stale build cleanup behavior."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import raidar.runner as runner
from raidar.runtime import pipeline


class _AdapterStub:
    def __init__(
        self,
        *,
        import_path: str | None = None,
        excluded_keys: set[str] | None = None,
        local_secret_files: dict[str, Path] | None = None,
    ) -> None:
        self._import_path = import_path
        self._excluded_keys = excluded_keys or set()
        self._local_secret_files = local_secret_files or {}

    def runtime_env(self) -> dict[str, str]:
        return {"ADAPTER_FLAG": "1", "COMPOSE_BAKE": "1"}

    def harbor_harness_import_path(self) -> str | None:
        return self._import_path

    def excluded_run_env_keys(self) -> set[str]:
        return self._excluded_keys

    def local_secret_files(self) -> dict[str, Path]:
        return self._local_secret_files


@pytest.fixture
def repo_tmp_agentic_eval_home(monkeypatch) -> Path:
    repo_tmp_root = Path(__file__).resolve().parents[2] / ".tmp"
    repo_tmp_root.mkdir(exist_ok=True)
    fake_home = Path(tempfile.mkdtemp(prefix="agentic-eval-home-", dir=repo_tmp_root))
    monkeypatch.setattr(runner.Path, "home", classmethod(lambda cls: fake_home))
    try:
        yield fake_home
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)


def test_build_harbor_run_env_preserves_standard_harness_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(_AdapterStub())

    assert env["ADAPTER_FLAG"] == "1"
    assert env["COMPOSE_BAKE"] == "false"
    assert env["OPENAI_API_KEY"] == "test-openai-key"
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" not in env
    assert "DOCKER_CONFIG" not in env


def test_build_harbor_run_env_uses_secret_files_for_custom_harnesses(
    monkeypatch,
    repo_tmp_agentic_eval_home: Path,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(
        _AdapterStub(import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent")
    )

    assert env["ADAPTER_FLAG"] == "1"
    assert env["COMPOSE_BAKE"] == "false"
    assert "OPENAI_API_KEY" not in env
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" in env
    secret_file = runner.Path(env["AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY"])
    assert secret_file.exists()
    assert secret_file.is_relative_to(repo_tmp_agentic_eval_home)
    assert secret_file.read_text(encoding="utf-8") == "test-openai-key"
    assert "DOCKER_CONFIG" not in env


def test_build_harbor_run_env_uses_local_secret_files_for_custom_harnesses(
    repo_tmp_agentic_eval_home: Path,
    tmp_path: Path,
) -> None:
    auth_json_path = tmp_path / "auth.json"
    auth_json_path.write_text('{"access_token":"token"}', encoding="utf-8")

    env = runner._build_harbor_run_env(
        _AdapterStub(
            import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent",
            local_secret_files={"CODEX_AUTH_JSON": auth_json_path},
        )
    )

    assert env["ADAPTER_FLAG"] == "1"
    secret_file = runner.Path(env["AGENTIC_EVAL_SECRET_FILE_CODEX_AUTH_JSON"])
    assert secret_file.exists()
    assert secret_file.is_relative_to(repo_tmp_agentic_eval_home)
    assert secret_file.read_text(encoding="utf-8") == '{"access_token":"token"}'


def test_build_harbor_run_env_excludes_adapter_blocked_env_keys(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    env = runner._build_harbor_run_env(
        _AdapterStub(
            import_path="raidar.agents.harbor_agents.cli_agents:CodexCliHarborAgent",
            excluded_keys={"OPENAI_API_KEY"},
        )
    )

    assert "OPENAI_API_KEY" not in env
    assert "AGENTIC_EVAL_SECRET_FILE_OPENAI_API_KEY" not in env


class _ExecAdapterStub:
    def build_harbor_command(self, *, task_path: Path, job_name: str, jobs_dir: Path) -> list[str]:
        del task_path, job_name, jobs_dir
        return ["harbor", "run"]

    def validate(self) -> None:
        return None

    def prepare_workspace(self, workspace: Path) -> None:
        del workspace

    def runtime_env(self) -> dict[str, str]:
        return {}

    def harbor_harness_import_path(self) -> str | None:
        return None

    def excluded_run_env_keys(self) -> set[str]:
        return set()

    def local_secret_files(self) -> dict[str, Path]:
        return {}

    def execution_metadata(self) -> dict[str, str]:
        return {}


def _patch_prepare_workspace_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    built_images: dict[str, dict[str, str]],
    preflight_calls: list[str],
    runtime_preflight_calls: list[str],
) -> None:
    monkeypatch.setattr(runner, "_raidar_cache_root", lambda: tmp_path / ".cache" / "raidar")
    monkeypatch.setattr(runner, "_maybe_run_cache_maintenance", lambda **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "_run_starter_preflight_install",
        lambda workspace, env: preflight_calls.append(f"install:{workspace.name}"),
    )
    monkeypatch.setattr(
        runner,
        "_run_starter_preflight_command",
        lambda workspace, env, command: preflight_calls.append(
            f"{workspace.name}:{' '.join(command)}"
        ),
    )
    monkeypatch.setattr(
        runner,
        "_inspect_docker_image_labels",
        lambda image_name, run_env: built_images.get(image_name),
    )
    monkeypatch.setattr(
        runner,
        "_ensure_harbor_runtime_preflight",
        lambda *, image_ref, run_env, log_dir: runtime_preflight_calls.append(image_ref.image_name),
    )

    def fake_run_task_image_build(
        build_cmd: list[str], run_env: dict[str, str], *, timeout_sec: int
    ):
        del run_env, timeout_sec
        image_name = build_cmd[build_cmd.index("--tag") + 1]
        labels: dict[str, str] = {}
        label_indices = [idx for idx, token in enumerate(build_cmd) if token == "--label"]
        for idx in label_indices:
            key, value = build_cmd[idx + 1].split("=", 1)
            labels[key] = value
        built_images[image_name] = labels
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(build_cmd, 0, stdout="built", stderr="")
        )

    monkeypatch.setattr(runner, "_run_task_image_build", fake_run_task_image_build)


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


def test_cleanup_stale_harbor_build_processes_ignores_ps_permission_error(
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        del args, kwargs
        raise PermissionError("[Errno 1] Operation not permitted: 'ps'")

    killed: list[tuple[int, signal.Signals]] = []

    def fake_kill(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.os, "kill", fake_kill)

    runner.cleanup_stale_harbor_build_processes()

    assert killed == []


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


def test_ensure_harbor_runtime_preflight_runs_git_check(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        del kwargs
        calls.append(list(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout="git version 2.51.0\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-abcd1234",
        cache_key="cache-key",
        tag="task-env-codex-cli-abcd1234",
    )
    runner._ensure_harbor_runtime_preflight(
        image_ref=image_ref,
        run_env={"PATH": os.environ.get("PATH", "")},
        log_dir=tmp_path,
    )

    assert calls[0] == ["docker", "run", "--rm", image_ref.image_name, "git", "--version"]
    assert calls[1][:8] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
    ]
    assert calls[1][8] == image_ref.image_name
    assert (tmp_path / "runtime-git-preflight.log").read_text(encoding="utf-8") == (
        "git version 2.51.0\n"
    )


def test_ensure_harbor_runtime_preflight_raises_when_git_missing(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(*args, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            args[0],
            127,
            stdout="",
            stderr="bash: git: command not found\n",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-abcd1234",
        cache_key="cache-key",
        tag="task-env-codex-cli-abcd1234",
    )
    with pytest.raises(RuntimeError, match="Harbor runtime preflight failed"):
        runner._ensure_harbor_runtime_preflight(
            image_ref=image_ref,
            run_env={},
            log_dir=tmp_path,
        )


def test_ensure_harbor_runtime_preflight_runs_isolation_probe(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(*args, **kwargs):
        del kwargs
        commands.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-abcd1234",
        cache_key="cache-key",
        tag="task-env-codex-cli-abcd1234",
    )

    runner._ensure_harbor_runtime_preflight(
        image_ref=image_ref,
        run_env={},
        log_dir=tmp_path,
    )

    assert commands[0] == [
        "docker",
        "run",
        "--rm",
        "raidar-task-env:task-env-codex-cli-abcd1234",
        "git",
        "--version",
    ]
    assert commands[1][:8] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
    ]
    assert commands[1][8] == "raidar-task-env:task-env-codex-cli-abcd1234"
    assert any("test ! -d /tmp/agentic-eval-secrets" in part for part in commands[1])
    assert (tmp_path / "runtime-isolation-preflight.log").read_text(encoding="utf-8") == "ok\n"


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


def test_ensure_starter_preflight_skips_test_command_without_tests(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    task_dir = tmp_path / "scenario"
    workspace.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            name="sample-task",
            verification=SimpleNamespace(
                required_commands=[["bun", "run", "test"], ["bun", "run", "lint"]]
            ),
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    context = SimpleNamespace(
        workspace=workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: False)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.ensure_starter_preflight(request, context)

    assert calls == [["bun", "install", "--frozen-lockfile"], ["bun", "run", "lint"]]


def test_ensure_starter_preflight_reuses_repo_local_cache_across_invocations(
    monkeypatch, tmp_path: Path
) -> None:
    task_dir = tmp_path / "scenario"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            verification=SimpleNamespace(required_commands=[["bun", "run", "lint"]])
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    workspace_one = tmp_path / "workspace-one"
    workspace_two = tmp_path / "workspace-two"
    workspace_one.mkdir(parents=True, exist_ok=True)
    workspace_two.mkdir(parents=True, exist_ok=True)
    context_one = SimpleNamespace(
        workspace=workspace_one,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )
    context_two = SimpleNamespace(
        workspace=workspace_two,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: True)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    first_hit = runner.ensure_starter_preflight(request, context_one)
    second_hit = runner.ensure_starter_preflight(request, context_two)

    assert first_hit is False
    assert second_hit is True
    assert calls == [["bun", "install", "--frozen-lockfile"], ["bun", "run", "lint"]]


def test_ensure_starter_preflight_uses_workspace_local_runtime_env(
    monkeypatch, tmp_path: Path
) -> None:
    task_dir = tmp_path / "scenario"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            verification=SimpleNamespace(required_commands=[["bun", "run", "lint"]])
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    context = SimpleNamespace(
        workspace=workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    envs: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        envs.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: True)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    runner.ensure_starter_preflight(request, context)

    expected_tmp = workspace / ".tmp"
    expected_cache = workspace / ".cache"
    expected_uv_cache = expected_cache / "uv"
    expected_bun_cache = expected_cache / "bun"

    assert envs
    for env in envs:
        assert env["TMPDIR"] == str(expected_tmp)
        assert env["TMP"] == str(expected_tmp)
        assert env["TEMP"] == str(expected_tmp)
        assert env["XDG_CACHE_HOME"] == str(expected_cache)
        assert env["UV_CACHE_DIR"] == str(expected_uv_cache)
        assert env["BUN_INSTALL_CACHE_DIR"] == str(expected_bun_cache)

    assert expected_tmp.is_dir()
    assert expected_uv_cache.is_dir()
    assert expected_bun_cache.is_dir()


def test_ensure_starter_preflight_runs_against_baseline_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    task_dir = tmp_path / "scenario"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "scenario.yaml").write_text(
        "name: sample\nscenario_revision: v001\n", encoding="utf-8"
    )

    request = SimpleNamespace(
        scenario=SimpleNamespace(
            verification=SimpleNamespace(required_commands=[["bun", "run", "lint"]])
        ),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario_dir=task_dir,
    )
    baseline_workspace = tmp_path / "baseline-workspace"
    run_workspace = tmp_path / "run-workspace"
    baseline_workspace.mkdir(parents=True, exist_ok=True)
    run_workspace.mkdir(parents=True, exist_ok=True)
    context = SimpleNamespace(
        workspace=run_workspace,
        baseline_workspace=baseline_workspace,
        baseline_cache_key="baseline-cache-key",
        starter_source=SimpleNamespace(fingerprint="abc123"),
    )

    called_workspaces: list[Path] = []

    def fake_install(workspace: Path, env: dict[str, str]) -> None:
        del env
        called_workspaces.append(workspace)

    def fake_command(workspace: Path, env: dict[str, str], command: list[str]) -> None:
        del env, command
        called_workspaces.append(workspace)

    monkeypatch.setattr(
        runner,
        "_preflight_cache_file",
        lambda cache_key: tmp_path / "preflight" / f"{cache_key}.ok.json",
    )
    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(runner, "_workspace_has_tests", lambda _workspace: True)
    monkeypatch.setattr(runner, "_run_starter_preflight_install", fake_install)
    monkeypatch.setattr(runner, "_run_starter_preflight_command", fake_command)

    runner.ensure_starter_preflight(request, context)

    assert called_workspaces == [baseline_workspace, baseline_workspace]


def test_cache_key_lock_reclaims_dead_owner_immediately(tmp_path: Path, monkeypatch) -> None:
    lock_root = tmp_path / "locks"
    lock_dir = lock_root / "image-dead.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "owner.json").write_text(
        json.dumps({"pid": 999999, "created_at": "2026-04-10T00:00:00+00:00"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: lock_root)

    with runner._cache_key_lock("image-dead", timeout_sec=1):
        assert lock_dir.exists()
        payload = json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()

    assert not lock_dir.exists()


def test_ensure_task_image_writes_log_and_raises_on_build_failure(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: False)

    def fake_run(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(command, 2, stdout="build-out", stderr="build-err")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Task image build failed"):
        runner._ensure_task_image(
            task_bundle_path=bundle_path,
            image_ref=image_ref,
            harness="codex-cli",
            run_env={},
            log_dir=tmp_path / "logs",
            task_timeout_sec=300,
        )

    build_log = tmp_path / "logs" / "task-image-build.log"
    assert build_log.exists()
    text = build_log.read_text(encoding="utf-8")
    assert "build-out" in text
    assert "build-err" in text


def test_task_image_reference_is_content_addressed_by_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_TASK_IMAGE_PREFIX", "custom-task-env")
    bundle_path = tmp_path / "bundle"
    app_dir = bundle_path / "environment" / "app"
    tests_dir = bundle_path / "tests"
    app_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (bundle_path / "environment" / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (app_dir / "package.json").write_text("{}", encoding="utf-8")
    (app_dir / "bun.lock").write_text("", encoding="utf-8")
    (tests_dir / "test.sh").write_text("echo first\n", encoding="utf-8")

    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )
    other_request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )

    image_ref = runner._task_image_reference(request, bundle_path)
    other_ref = runner._task_image_reference(other_request, bundle_path)

    assert image_ref is not None
    assert image_ref.image_name.startswith("custom-task-env:task-env-codex-cli-")
    assert other_ref is not None
    assert other_ref.image_name.startswith("custom-task-env:task-env-gemini-")
    assert image_ref.cache_key != other_ref.cache_key

    (tests_dir / "test.sh").write_text("echo second\n", encoding="utf-8")
    changed_tests_ref = runner._task_image_reference(request, bundle_path)

    assert changed_tests_ref is not None
    assert changed_tests_ref.cache_key != image_ref.cache_key


def test_task_image_reference_respects_reuse_disable_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_path = tmp_path / "bundle"
    app_dir = bundle_path / "environment" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (bundle_path / "environment" / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (app_dir / "package.json").write_text("{}", encoding="utf-8")

    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )

    assert runner._task_image_reference(request, bundle_path) is not None

    monkeypatch.setenv("RAIDAR_TASK_IMAGE_REUSE", "0")

    assert runner._task_image_reference(request, bundle_path) is None


def test_task_image_build_command_uses_classic_docker_build(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM oven/bun:1\n", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    command = runner._task_image_build_command(
        image_ref,
        dockerfile,
        tmp_path,
        harness="codex-cli",
    )

    assert command[:2] == ["docker", "build"]
    assert "--load" not in command


def test_render_environment_dockerfile_includes_visual_tooling_dependencies() -> None:
    request = SimpleNamespace(
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
        scenario=SimpleNamespace(visual=object()),
    )

    dockerfile = runner._render_environment_dockerfile(request)

    assert "git" in dockerfile
    assert "ripgrep" in dockerfile
    assert "file" in dockerfile
    assert "libatk-bridge2.0-0t64" in dockerfile
    assert "libcups2t64" in dockerfile
    assert "libcairo2" in dockerfile
    assert "libpango-1.0-0" in dockerfile
    assert dockerfile.index("RUN bunx playwright install chromium") < dockerfile.index(
        "COPY app/ /app/"
    )


def test_ensure_task_image_returns_immediately_when_image_exists(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: True)
    preflight_calls: list[str] = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when image already exists")

    monkeypatch.setattr(runner.subprocess, "run", fail_if_called)
    monkeypatch.setattr(
        runner,
        "_ensure_harbor_runtime_preflight",
        lambda *, image_ref, run_env, log_dir: preflight_calls.append(
            f"{image_ref.image_name}:{log_dir.name}"
        ),
    )

    cache_hit = runner._ensure_task_image(
        task_bundle_path=bundle_path,
        image_ref=image_ref,
        harness="codex-cli",
        run_env={},
        log_dir=tmp_path / "logs",
        task_timeout_sec=300,
    )

    assert cache_hit is True
    assert preflight_calls == [f"{image_ref.image_name}:logs"]
    assert not (tmp_path / "logs" / "task-image-build.log").exists()


def test_ensure_task_image_rebuilds_when_cached_image_fails_runtime_preflight(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-cachekey",
        cache_key="cachekey",
        tag="task-env-codex-cli-cachekey",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: True)

    build_calls: list[list[str]] = []

    def fake_build(build_cmd, run_env, *, timeout_sec):
        del run_env, timeout_sec
        build_calls.append(build_cmd)
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(
                build_cmd, 0, stdout="build-ok", stderr=""
            )
        )

    preflight_attempts: list[str] = []

    def fake_runtime_preflight(*, image_ref, run_env, log_dir):
        del run_env, log_dir
        preflight_attempts.append(image_ref.image_name)
        if len(preflight_attempts) < 3:
            raise RuntimeError("git missing")

    monkeypatch.setattr(runner, "_run_task_image_build", fake_build)
    monkeypatch.setattr(runner, "_ensure_harbor_runtime_preflight", fake_runtime_preflight)

    cache_hit = runner._ensure_task_image(
        task_bundle_path=bundle_path,
        image_ref=image_ref,
        harness="codex-cli",
        run_env={},
        log_dir=tmp_path / "logs",
        task_timeout_sec=300,
    )

    assert cache_hit is False
    assert len(build_calls) == 1
    assert len(preflight_attempts) == 3


def test_ensure_task_image_writes_log_and_raises_on_build_timeout(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_path = tmp_path / "bundle"
    docker_context = bundle_path / "environment"
    docker_context.mkdir(parents=True, exist_ok=True)
    (docker_context / "Dockerfile").write_text("FROM oven/bun:1\n", encoding="utf-8")
    (docker_context / "app").mkdir(parents=True, exist_ok=True)
    (docker_context / "app" / "package.json").write_text("{}", encoding="utf-8")
    image_ref = runner.TaskImageRef(
        image_name="raidar-task-env:task-env-codex-cli-timeout",
        cache_key="timeout-key",
        tag="task-env-codex-cli-timeout",
    )

    monkeypatch.setattr(runner, "_cache_lock_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(
        runner,
        "_task_image_cache_metadata_path",
        lambda cache_key: tmp_path / "image-metadata" / f"{cache_key}.json",
    )
    monkeypatch.setattr(runner, "_task_image_cache_hit", lambda *_args, **_kwargs: False)

    def fake_build(build_cmd, run_env, *, timeout_sec):
        del run_env
        return runner.TaskImageBuildResult(
            completed_process=subprocess.CompletedProcess(
                build_cmd,
                returncode=124,
                stdout="partial-out",
                stderr="partial-err",
            ),
            timed_out=True,
            timeout_sec=timeout_sec,
        )

    monkeypatch.setattr(runner, "_run_task_image_build", fake_build)

    with pytest.raises(RuntimeError, match="Task image build timed out after 300s"):
        runner._ensure_task_image(
            task_bundle_path=bundle_path,
            image_ref=image_ref,
            harness="codex-cli",
            run_env={},
            log_dir=tmp_path / "logs",
            task_timeout_sec=300,
        )

    build_log = tmp_path / "logs" / "task-image-build.log"
    assert build_log.exists()
    text = build_log.read_text(encoding="utf-8")
    assert "partial-out" in text
    assert "partial-err" in text


def test_prepare_workspace_phase_reuses_prep_and_task_image_across_invocations(
    monkeypatch, tmp_path: Path
) -> None:
    scenario_dir = tmp_path / "scenarios" / "hello-world-smoke" / "v001"
    starter_dir = scenario_dir / "starter"
    prompt_dir = scenario_dir / "prompt"
    (starter_dir / "src").mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (starter_dir / "package.json").write_text(
        json.dumps({"scripts": {"lint": "echo lint"}}),
        encoding="utf-8",
    )
    (starter_dir / "bun.lock").write_text("", encoding="utf-8")
    (starter_dir / "src" / "index.tsx").write_text(
        "export const App = () => null;\n", encoding="utf-8"
    )
    (prompt_dir / "task.md").write_text("Print hello world\n", encoding="utf-8")
    (scenario_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "name: hello-world-smoke",
                "scenario_revision: v001",
                "description: smoke task",
                "difficulty: easy",
                "category: greenfield-ui",
                "timeout_sec: 1800",
                "starter:",
                "  root: starter",
                "verification:",
                "  gates: []",
                "  required_commands:",
                "    - [bun, run, lint]",
                "acceptance: {}",
                "metrics:",
                "  - {type: core, id: functional}",
                "  - {type: core, id: acceptance}",
                "  - {type: core, id: verification-stability}",
                "  - {type: core, id: execution-validity}",
                "  - {type: core, id: resource-efficiency}",
                "prompt:",
                "  entry: prompt/task.md",
                "",
            ]
        ),
        encoding="utf-8",
    )

    adapter = _ExecAdapterStub()
    config = SimpleNamespace(
        harness=runner.Harness.CODEX_CLI,
        timeout_sec=300,
    )
    scenario = runner.load_scenario(scenario_dir / "scenario.yaml")
    request_one = runner.RunRequest(
        scenario=scenario,
        config=config,
        scenario_dir=scenario_dir,
        execution_dir=tmp_path / "experiments" / "run-one",
        repeat_index=1,
    )
    request_two = runner.RunRequest(
        scenario=scenario,
        config=config,
        scenario_dir=scenario_dir,
        execution_dir=tmp_path / "experiments" / "run-two",
        repeat_index=1,
    )

    built_images: dict[str, dict[str, str]] = {}
    preflight_calls: list[str] = []
    runtime_preflight_calls: list[str] = []

    _patch_prepare_workspace_dependencies(
        monkeypatch,
        tmp_path=tmp_path,
        built_images=built_images,
        preflight_calls=preflight_calls,
        runtime_preflight_calls=runtime_preflight_calls,
    )
    monkeypatch.setattr(pipeline, "resolve_adapter", lambda _config: adapter)

    phase_one = pipeline.prepare_workspace_phase(request_one)
    phase_two = pipeline.prepare_workspace_phase(request_two)

    assert phase_one.cache_metadata["baseline"]["hit"] is False
    assert phase_one.cache_metadata["baseline"]["status"] == "miss"
    assert phase_two.cache_metadata["baseline"]["hit"] is True
    assert phase_two.cache_metadata["baseline"]["status"] == "hit"
    assert phase_one.cache_metadata["baseline"]["metadata_path"].endswith("metadata.json")
    assert phase_two.cache_metadata["baseline"]["complete"] is True
    assert phase_one.cache_metadata["preflight"]["hit"] is False
    assert phase_two.cache_metadata["preflight"]["hit"] is True
    assert phase_one.cache_metadata["image"]["hit"] is False
    assert phase_two.cache_metadata["image"]["hit"] is True
    assert (phase_one.layout.harbor_dir / "task-image-build.log").exists()
    assert not (phase_two.layout.harbor_dir / "task-image-build.log").exists()
    assert preflight_calls == ["install:workspace", "workspace:bun run lint"]
    assert phase_one.cache_metadata["image_key"] == phase_two.cache_metadata["image_key"]
    assert phase_one.cache_metadata["image_tag"] == phase_two.cache_metadata["image_tag"]
    expected_image = f"{runner.task_image_prefix()}:{phase_one.cache_metadata['image_tag']}"
    assert runtime_preflight_calls
    assert all(image_name == expected_image for image_name in runtime_preflight_calls)


def test_prepare_workspace_phase_validates_before_initializing_run(monkeypatch) -> None:
    class FailingAdapter:
        def validate(self) -> None:
            raise ValueError("invalid harness")

    def fail_initialize_run(_request):
        raise AssertionError("initialize_run should not run before adapter validation")

    monkeypatch.setattr(runner, "initialize_run", fail_initialize_run)
    monkeypatch.setattr(pipeline, "resolve_adapter", lambda _config: FailingAdapter())
    request = SimpleNamespace(config=SimpleNamespace())

    with pytest.raises(ValueError, match="invalid harness"):
        pipeline.prepare_workspace_phase(request)


def test_execute_harbor_phase_uses_empty_metrics_when_terminated_and_usage_missing(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    harbor_result = runner.HarborExecutionResult(
        terminated_early=True,
        termination_reason="Harbor trial exception: Agent execution timed out after 300.0 seconds",
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=tmp_path / "trial",
    )

    def fake_collect_process_metrics(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Missing token usage metrics for harness `gemini` in trial `/tmp/trial`."
        )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(runner, "collect_process_metrics", fake_collect_process_metrics)
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])

    result = pipeline.execute_harbor_phase(request, phase)

    assert result.terminated_early is True
    assert result.termination_reason == harbor_result.termination_reason
    assert result.process_metrics.uncached_input_tokens == 0
    assert result.process_metrics.output_tokens == 0
    assert result.process_metrics.command_count == 0


def test_execute_harbor_phase_recovers_timeout_when_verifier_outputs_exist(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="codex-cli")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir(parents=True, exist_ok=True)
    harbor_result = runner.HarborExecutionResult(
        terminated_early=True,
        termination_reason="Timeout expired after 420s before trial result.json was written.",
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=trial_dir,
    )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(
        runner,
        "collect_process_metrics",
        lambda *args, **kwargs: runner._empty_process_metrics(),
    )
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])
    verifier_outputs = runner.terminated_outputs(None)
    monkeypatch.setattr(
        runner,
        "_load_verifier_outputs",
        lambda _trial_dir: (verifier_outputs, None),
    )

    result = pipeline.execute_harbor_phase(request, phase)

    assert result.terminated_early is False
    assert result.termination_reason is None
    assert result.outputs == verifier_outputs


def test_execute_harbor_phase_raises_when_usage_missing_without_termination(
    monkeypatch, tmp_path: Path
) -> None:
    request = SimpleNamespace(
        scenario=object(),
        config=SimpleNamespace(harness=SimpleNamespace(value="gemini")),
    )
    phase = SimpleNamespace(
        harbor_request=object(),
        layout=SimpleNamespace(start_time=datetime.now(UTC)),
    )
    harbor_result = runner.HarborExecutionResult(
        terminated_early=False,
        termination_reason=None,
        job_dir=tmp_path / "jobs" / "orchestrator-run-01",
        trial_dir=tmp_path / "trial",
    )

    def fake_collect_process_metrics(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Missing token usage metrics for harness `gemini` in trial `/tmp/trial`."
        )

    monkeypatch.setattr(runner, "execute_harbor", lambda _request: harbor_result)
    monkeypatch.setattr(runner, "collect_process_metrics", fake_collect_process_metrics)
    monkeypatch.setattr(runner, "collect_trace_events", lambda *args, **kwargs: [])

    with pytest.raises(RuntimeError, match="Missing token usage metrics"):
        pipeline.execute_harbor_phase(request, phase)


def test_execute_harbor_preserves_trial_dir_on_timeout(monkeypatch, tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "orchestrator-run-01"
    trial_dir = job_dir / "bundle__abc123"
    (trial_dir / "agent").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        runner,
        "_run_harbor_process",
        lambda **kwargs: "Timeout expired after 420s before trial result.json was written.",
    )
    monkeypatch.setattr(runner, "cleanup_stale_harbor_resources", lambda: None)

    adapter = SimpleNamespace(
        build_harbor_command=lambda **kwargs: ["harbor", "run"],
    )
    request = runner.HarborExecutionRequest(
        adapter=adapter,
        workspace=tmp_path,
        task_bundle_path=tmp_path / "bundle",
        jobs_dir=jobs_dir,
        run_harbor_dir=tmp_path / "harbor",
        run_id="run-01",
        timeout_sec=420,
        run_env={},
    )

    result = runner.execute_harbor(request)

    assert result.terminated_early is True
    assert result.trial_dir == trial_dir
