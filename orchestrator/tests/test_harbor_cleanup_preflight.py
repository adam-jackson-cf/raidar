"""Focused Harbor runtime tests."""

# ruff: noqa: F403, F405
from tests.harbor_runtime_support import *  # noqa: F403


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
