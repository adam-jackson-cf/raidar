"""The run-scoped Docker environment's engine fast path (N1 overhead fixes)."""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from harbor.environments.base import ExecResult

from raidar.engine.trial import environment as env_module
from raidar.engine.trial.environment import RunScopedDockerEnvironment, engine_command


@pytest.mark.parametrize(
    ("compose_args", "stdin", "expected"),
    [
        (
            ["exec", "-w", "/app", "-e", "K=V", "-u", "agent", "main", "bash", "-c", "pwd"],
            False,
            ["exec", "-w", "/app", "-e", "K=V", "-u", "agent", "cid", "bash", "-c", "pwd"],
        ),
        (
            ["exec", "-T", "-u", "root", "main", "tar", "-xf", "-", "-C", "/app"],
            True,
            ["exec", "-i", "-u", "root", "cid", "tar", "-xf", "-", "-C", "/app"],
        ),
        (["cp", "/host/workdir/.", "main:/app"], False, ["cp", "/host/workdir/.", "cid:/app"]),
        (["cp", "main:/tmp/x.tar", "/host/x.tar"], False, ["cp", "cid:/tmp/x.tar", "/host/x.tar"]),
    ],
)
def test_main_service_exec_and_cp_translate_flag_for_flag(
    compose_args: list[str], stdin: bool, expected: list[str]
) -> None:
    assert engine_command(compose_args, "cid", stdin=stdin) == expected


@pytest.mark.parametrize(
    "compose_args",
    [
        [],
        ["down", "--remove-orphans"],
        ["stop", "main"],
        ["exec", "sidecar", "sh", "-c", "true"],
        ["exec", "--privileged", "main", "true"],
        ["exec", "main"],
        ["cp", "sidecar:/x", "/host/x"],
        ["cp", "main:/a", "main:/b"],
        ["cp", "/a", "/b"],
    ],
)
def test_anything_else_stays_on_the_compose_path(compose_args: list[str]) -> None:
    assert engine_command(compose_args, "cid", stdin=False) is None


def _bare_env(container: str | None) -> RunScopedDockerEnvironment:
    env = object.__new__(RunScopedDockerEnvironment)
    env._main_container = container  # noqa: SLF001
    env._is_windows_container = False  # noqa: SLF001
    env._post_start = []  # noqa: SLF001  (as the constructor leaves it)
    env._synced = {}  # noqa: SLF001

    async def resolve() -> str | None:
        return container

    env._resolve_main_container = resolve  # type: ignore[method-assign]  # noqa: SLF001
    return env


async def test_translated_commands_run_on_the_engine_and_the_rest_on_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    async def compose(self: Any, command: list[str], **_: Any) -> ExecResult:
        calls.append(("compose", command))
        return ExecResult(return_code=0)

    async def engine(self: Any, argv: list[str], **_: Any) -> ExecResult:
        calls.append(("engine", argv))
        return ExecResult(return_code=0)

    monkeypatch.setattr(
        "harbor.environments.docker.docker.DockerEnvironment._run_docker_compose_command", compose
    )
    monkeypatch.setattr(RunScopedDockerEnvironment, "_run_engine_command", engine)
    env = _bare_env("cid")
    await env._run_docker_compose_command(["exec", "-u", "root", "main", "true"])  # noqa: SLF001
    await env._run_docker_compose_command(["down"])  # noqa: SLF001
    unresolved = _bare_env(None)
    await unresolved._run_docker_compose_command(["exec", "main", "true"])  # noqa: SLF001
    assert calls == [
        ("engine", ["exec", "-u", "root", "cid", "true"]),
        ("compose", ["down"]),
        ("compose", ["exec", "main", "true"]),
    ]


def _tar_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


async def test_excluded_download_is_one_streamed_tar_with_harbors_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    payload = _tar_of({"./main.py": b"print(1)\n", "./pkg/mod.py": b"x = 1\n"})

    class FakeProcess:
        returncode = 0

        def __init__(self, sink: Any) -> None:
            self.sink = sink

        async def communicate(self) -> tuple[None, bytes]:
            self.sink.write(payload)
            return None, b""

    async def fake_exec(*argv: str, stdout: Any, **_: Any) -> FakeProcess:
        seen.append(argv)
        return FakeProcess(stdout)

    monkeypatch.setattr(env_module.asyncio, "create_subprocess_exec", fake_exec)
    env = _bare_env("cid")
    monkeypatch.setattr(env, "_compose_env_vars", lambda include_os_env=True: {}, raising=False)
    await env.download_dir_with_exclusions(
        source_dir="/app", target_dir=tmp_path / "app", exclude=[".venv", "*.pyc"]
    )
    assert seen == [
        (
            "docker",
            "exec",
            "-u",
            "root",
            "cid",
            "tar",
            "cf",
            "-",
            "--exclude=.venv",
            "--exclude=*.pyc",
            "-C",
            "/app",
            ".",
        )
    ]
    assert (tmp_path / "app" / "main.py").read_bytes() == b"print(1)\n"
    assert (tmp_path / "app" / "pkg" / "mod.py").read_bytes() == b"x = 1\n"


async def test_a_failed_stream_raises_like_harbors_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailedProcess:
        returncode = 2

        async def communicate(self) -> tuple[None, bytes]:
            return None, b"tar: /app: Cannot open"

    async def fake_exec(*_argv: str, **_: Any) -> FailedProcess:
        return FailedProcess()

    monkeypatch.setattr(env_module.asyncio, "create_subprocess_exec", fake_exec)
    env = _bare_env("cid")
    monkeypatch.setattr(env, "_compose_env_vars", lambda include_os_env=True: {}, raising=False)
    with pytest.raises(RuntimeError, match="code 2: tar: /app: Cannot open"):
        await env.download_dir_with_exclusions(
            source_dir="/app", target_dir=tmp_path / "app", exclude=[]
        )


def test_the_environment_is_not_mounted_and_declares_no_volumes() -> None:
    env = _bare_env("cid")
    env._enable_egress_control = False  # noqa: SLF001
    env._mounts = [  # noqa: SLF001
        {"type": "bind", "source": "/host/agent", "target": "/logs/agent"}  # type: ignore[list-item]
    ]
    assert env.capabilities.mounted is False
    env._mounts_compose_temp_dir = None  # noqa: SLF001
    path = env._write_mounts_compose_file()  # noqa: SLF001
    try:
        assert json.loads(path.read_text()) == {"services": {"main": {"volumes": []}}}
    finally:
        env._cleanup_mounts_compose_file()  # noqa: SLF001


async def test_preparing_logs_for_the_host_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _bare_env("cid")

    async def forbidden(*_: Any, **__: Any) -> ExecResult:
        raise AssertionError("no exec: nothing is mounted")

    monkeypatch.setattr(env, "exec", forbidden, raising=False)
    await env.prepare_logs_for_host()


async def test_start_warms_agent_imports_alongside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def compose_start(self: Any, force_build: bool) -> None:
        calls.append(f"start:{force_build}")

    async def warm() -> None:
        calls.append("warm")

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.start", compose_start)
    monkeypatch.setattr(env_module, "warm_agent_imports", warm)
    env = _bare_env("stale")
    await env.start(False)
    assert sorted(calls) == ["start:False", "warm"]
    assert env._main_container is None  # noqa: SLF001  (a new container is resolved again)


async def test_warm_agent_imports_loads_missing_modules_and_tolerates_absent_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "colorsys", raising=False)
    monkeypatch.setattr(env_module, "LAZY_AGENT_IMPORTS", ("colorsys", "raidar_no_such_module"))
    await env_module.warm_agent_imports()
    assert "colorsys" in sys.modules


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int) -> None:
        self.stdout_bytes = stdout
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, None]:  # noqa: A002
        return self.stdout_bytes, None


def _resolvable_env(
    monkeypatch: pytest.MonkeyPatch, stdout: bytes, returncode: int = 0
) -> tuple[RunScopedDockerEnvironment, list[tuple[str, ...]]]:
    spawned: list[tuple[str, ...]] = []

    async def fake_exec(*argv: str, **_: Any) -> _FakeProcess:
        spawned.append(argv)
        return _FakeProcess(stdout, returncode)

    monkeypatch.setattr(env_module.asyncio, "create_subprocess_exec", fake_exec)
    env = object.__new__(RunScopedDockerEnvironment)
    env._main_container = None  # noqa: SLF001
    env._is_windows_container = False  # noqa: SLF001
    env.session_id = "b00-p0-baseline__env-abc"
    env.environment_name = "fixture"
    monkeypatch.setattr(env, "_compose_env_vars", lambda include_os_env=True: {}, raising=False)
    return env, spawned


async def test_the_main_container_is_resolved_once_by_compose_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env, spawned = _resolvable_env(monkeypatch, b"4c6d0baac68c\n")
    assert await env._resolve_main_container() == "4c6d0baac68c"  # noqa: SLF001
    assert await env._resolve_main_container() == "4c6d0baac68c"  # noqa: SLF001
    assert len(spawned) == 1
    assert "label=com.docker.compose.service=main" in spawned[0]


@pytest.mark.parametrize(("stdout", "returncode"), [(b"", 0), (b"a\nb\n", 0), (b"a\n", 1)])
async def test_an_ambiguous_or_missing_container_falls_back_to_compose(
    monkeypatch: pytest.MonkeyPatch, stdout: bytes, returncode: int
) -> None:
    env, _ = _resolvable_env(monkeypatch, stdout, returncode)
    assert await env._resolve_main_container() is None  # noqa: SLF001


async def test_engine_commands_keep_harbors_output_and_failure_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env, spawned = _resolvable_env(monkeypatch, b"/app\n", 0)
    run = env._run_engine_command  # noqa: SLF001
    result = await run(
        ["exec", "cid", "pwd"], check=True, timeout_sec=None, stdin_data=None, on_output=None
    )
    assert (result.stdout, result.return_code, spawned[0]) == (
        "/app\n",
        0,
        ("docker", "exec", "cid", "pwd"),
    )
    failing, _ = _resolvable_env(monkeypatch, b"boom", 3)
    run = failing._run_engine_command  # noqa: SLF001
    unchecked = await run(
        ["exec", "cid", "false"], check=False, timeout_sec=None, stdin_data=None, on_output=None
    )
    assert unchecked.return_code == 3
    with pytest.raises(RuntimeError, match="Return code: 3"):
        await run(
            ["exec", "cid", "false"], check=True, timeout_sec=None, stdin_data=None, on_output=None
        )


async def test_stopping_the_environment_or_main_service_forgets_the_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop(self: Any, *_: Any) -> None:
        return None

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.stop", noop)
    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.stop_service", noop)
    env = _bare_env("cid")
    await env.stop_service("sidecar")
    assert env._main_container == "cid"  # noqa: SLF001
    await env.stop_service("main")
    assert env._main_container is None  # noqa: SLF001
    env._main_container = "cid"  # noqa: SLF001
    await env.stop(delete=True)
    assert env._main_container is None  # noqa: SLF001


async def test_the_final_agent_log_download_keeps_files_the_host_rewrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    container = {
        "/logs/agent/stage": {"trajectory.json": "container"},
        "/logs/agent": {"stage/trajectory.json": "container", "codex.txt": "v1"},
    }

    async def harbor_download(self: Any, source_dir: str, target_dir: Path | str) -> None:
        for name, text in container[source_dir].items():
            path = Path(target_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    monkeypatch.setattr(
        "harbor.environments.docker.docker.DockerEnvironment.download_dir", harbor_download
    )
    env = _bare_env("cid")
    host = tmp_path / "agent"
    await env.download_dir("/logs/agent/stage", host / "stage")  # a component's mid-run sync
    (host / "stage" / "trajectory.json").write_text("host root trajectory, longer")
    container["/logs/agent"]["codex.txt"] = "v2"

    await env.download_dir("/logs/agent", host)  # Harbor's final download

    assert (host / "stage" / "trajectory.json").read_text() == "host root trajectory, longer"
    assert (host / "codex.txt").read_text() == "v2"  # untouched on the host: the container wins
    container["/logs/agent"]["codex.txt"] = "v3"
    await env.download_dir("/logs/agent", host)
    assert (host / "codex.txt").read_text() == "v3"
