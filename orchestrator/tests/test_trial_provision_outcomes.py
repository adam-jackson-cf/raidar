"""Provisioning outcomes the step checks rely on: a failed install, snapshot or check is recorded.

The environment is an in-memory fake; nothing runs in a container.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.environments.base import ExecResult
from harbor.models.trial.paths import TrialPaths
from harbor.trial.hooks import TrialEvent

from raidar.engine.trial import environment as env_module
from raidar.engine.trial import provision as provision_module
from raidar.engine.trial.environment import (
    RunScopedDockerEnvironment,
    engine_command,
    run_scope,
    trial_project_name,
)
from raidar.engine.trial.provision import (
    MAX_OUTPUT_CHARS,
    TrialProvision,
    install_trial_provisioning,
    parse_check_output,
    provision_record_check,
)

DOWNLOADED: dict[str, dict[str, bytes]] = {}


@pytest.fixture
def memory_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot directories and their digest live in memory, keyed by a virtual path."""
    DOWNLOADED.clear()
    counter = iter(range(1000))

    class _Dir:
        def __init__(self, prefix: str = "") -> None:
            self.name = f"/virtual/{prefix}{next(counter)}"

        def __enter__(self) -> str:
            return self.name

        def __exit__(self, *_: object) -> None:
            return None

    def digest(path: Path) -> str:
        files = DOWNLOADED[str(path)]
        return hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()

    monkeypatch.setattr(provision_module, "tempfile", SimpleNamespace(TemporaryDirectory=_Dir))
    monkeypatch.setattr(provision_module, "tree_digest", digest)


class _Env:
    """Execs answer from ``replies`` by their command; ``raises`` makes one command raise."""

    def __init__(self, *, replies: dict[str, Any] | None = None, raises: set[str] | None = None):
        self.replies = replies or {}
        self.raises = raises or set()
        self.records: list[dict[str, Any]] = []
        self.checks: list[str] = []
        self.hooks: list[Any] = []
        self.add_post_start_hook = self.hooks.append
        self.snapshot_files: dict[str, bytes] | None = {"app/main.py": b"print(1)\n"}

    async def exec(
        self, command: str, user: str | None = None, timeout_sec: int = 0, env: Any = None
    ) -> Any:
        command = command.removeprefix("umask 022; ")
        if ">>" in command:
            slug = command.rsplit("/", 1)[1].split(".jsonl", 1)[0]
            if slug in self.raises:
                raise RuntimeError("record write refused")
            encoded = command.split("printf %s ", 1)[1].split(" ", 1)[0]
            self.records.append(json.loads(base64.b64decode(encoded)))
            return SimpleNamespace(return_code=0, stdout="", stderr="")
        if command in self.raises:
            raise RuntimeError(f"{command} broke")
        if command.startswith("CHECK"):
            self.checks.append(command)
        return self.replies.get(command, SimpleNamespace(return_code=0, stdout="", stderr=""))

    async def upload_dir(self, source: str, target: str) -> None:
        return None

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path, exclude: list[str]
    ) -> None:
        if self.snapshot_files is None:
            raise RuntimeError("snapshot source missing")
        DOWNLOADED[str(target_dir)] = dict(self.snapshot_files)


def _provision(slug: str = "tool", **overrides: Any) -> TrialProvision:
    fields: dict[str, Any] = {
        "slug": slug,
        "payload": {},
        "install": f"INSTALL-{slug}",
        "check": f"CHECK-{slug}",
    }
    return TrialProvision(**{**fields, **overrides})


async def _run_one_step(
    env: _Env, provisions: list[TrialProvision], *, user: str = "agent"
) -> None:
    hooks: dict[TrialEvent, list[Any]] = {}
    trial = SimpleNamespace(
        task=SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(user=user))),
        agent_environment=env,
        add_hook=lambda event, hook: hooks.setdefault(event, []).append(hook),
    )
    install_trial_provisioning(cast(Any, trial), provisions)
    for at_start in env.hooks:
        await at_start()
    event = SimpleNamespace(result=SimpleNamespace(step_results=[SimpleNamespace(step_name="s1")]))
    for before_agent in hooks[TrialEvent.AGENT_START]:
        await before_agent(event)


def _verdict(record: dict[str, Any]) -> tuple[bool, str]:
    return provision_record_check(record, record["slug"])


async def test_the_persisted_snapshot_digest_follows_the_snapshot_content(
    memory_snapshots: None,
) -> None:
    digests = []
    for content in (b"print(1)\n", b"print(1)\n", b"print(2)\n"):
        env = _Env()
        env.snapshot_files = {"app/main.py": content}
        await _run_one_step(env, [_provision(snapshot="/app")])
        (record,) = env.records
        assert record["install"]["snapshot"] == "/app"
        assert _verdict(record)[0]
        digests.append(record["install"]["snapshot_digest"])

    assert digests[0] == digests[1]
    assert digests[0] != digests[2]
    assert all(isinstance(digest, str) and digest for digest in digests)


async def test_an_unreadable_snapshot_fails_the_install_and_no_check_runs(
    memory_snapshots: None,
) -> None:
    env = _Env()
    env.snapshot_files = None
    await _run_one_step(env, [_provision(snapshot="/app")])

    (record,) = env.records
    assert record["install"]["ok"] is False
    assert record["install"]["snapshot_digest"] is None
    assert record["install"]["output"] == "install failed: RuntimeError: snapshot source missing"
    assert record["check"] is None
    assert env.checks == []
    assert not _verdict(record)[0]


async def test_an_install_that_raises_is_recorded_not_propagated() -> None:
    env = _Env(raises={"INSTALL-tool"})
    await _run_one_step(env, [_provision()])

    (record,) = env.records
    assert record["install"]["ok"] is False
    assert record["install"]["output"] == "install failed: RuntimeError: INSTALL-tool broke"
    assert env.checks == []
    assert not _verdict(record)[0]


@pytest.mark.parametrize("user", ["agent", "root"])
async def test_a_check_that_cannot_run_is_recorded_as_a_failed_check(user: str) -> None:
    env = _Env(raises={"CHECK-tool"})
    await _run_one_step(env, [_provision()], user=user)

    (record,) = env.records
    assert record["install"]["ok"] is True
    assert record["check"] == {
        "ok": False,
        "error": "check exec failed: RuntimeError: CHECK-tool broke",
    }
    assert not _verdict(record)[0]


async def test_a_failing_check_keeps_the_tail_of_its_output_and_the_values_it_printed() -> None:
    noisy = SimpleNamespace(
        return_code=2, stdout="tree_digest=abc\n" + "o" * 9000, stderr="drifted\n"
    )
    env = _Env(replies={"CHECK-tool": noisy})
    await _run_one_step(env, [_provision()])

    (record,) = env.records
    check = record["check"]
    assert check["ok"] is False
    assert check["values"] == {"tree_digest": "abc"}
    assert len(check["error"]) == MAX_OUTPUT_CHARS
    assert check["error"].endswith("drifted\n")
    passed, detail = _verdict(record)
    assert not passed
    assert detail.startswith("tool: check failed:")


async def test_a_refused_record_write_does_not_stop_the_other_provisions_recording() -> None:
    env = _Env(raises={"first"})
    await _run_one_step(env, [_provision("first"), _provision("second")])

    assert [record["slug"] for record in env.records] == ["second"]
    assert _verdict(env.records[0])[0]


def test_check_output_keeps_only_key_value_lines_and_the_last_value_of_a_key() -> None:
    stdout = "noise\n=orphan\nbad key=1\nname=first\n  spaced_key = x \nname= last \n"
    assert parse_check_output(stdout) == {"name": "last"}


def _started_env(
    monkeypatch: pytest.MonkeyPatch, trial_dir: Path, *, container: str | None, order: list[str]
) -> RunScopedDockerEnvironment:
    """An environment whose compose start and container inspection are in-memory."""

    async def compose_start(self: Any, force_build: bool) -> None:
        order.append("start")

    async def warm() -> None:
        return None

    async def resolve() -> str | None:
        return container

    async def inspect(argv: list[str], **_: Any) -> ExecResult:
        order.append("inspect")
        return ExecResult(return_code=0, stdout="PATH\n")

    monkeypatch.setattr("harbor.environments.docker.docker.DockerEnvironment.start", compose_start)
    monkeypatch.setattr(env_module, "warm_agent_imports", warm)
    env = object.__new__(RunScopedDockerEnvironment)
    env._main_container = None  # noqa: SLF001
    env._is_windows_container = False  # noqa: SLF001
    env._post_start = []  # noqa: SLF001
    env._synced = {}  # noqa: SLF001
    env._sandbox_env_names = {}  # noqa: SLF001
    env._persistent_env = {}  # noqa: SLF001
    env._compose_task_env = {}  # noqa: SLF001
    env.trial_paths = TrialPaths(trial_dir)
    monkeypatch.setattr(env, "_resolve_main_container", resolve)
    monkeypatch.setattr(env, "_run_engine_command", inspect)
    return env


async def test_post_start_hooks_run_in_order_only_after_the_container_was_inspected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []
    env = _started_env(monkeypatch, tmp_path, container="cid", order=order)

    async def first() -> None:
        order.append("first")

    async def second() -> None:
        order.append("second")

    env.add_post_start_hook(first)
    env.add_post_start_hook(second)
    await env.start(False)

    assert order == ["start", "inspect", "first", "second"]


async def test_start_fails_and_runs_no_hook_when_the_main_container_cannot_be_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    order: list[str] = []
    env = _started_env(monkeypatch, tmp_path, container=None, order=order)

    async def hook() -> None:
        order.append("hook")

    env.add_post_start_hook(hook)
    with pytest.raises(RuntimeError, match="could not record sandbox environment names"):
        await env.start(False)

    assert order == ["start"]


def test_unchanged_environment_names_are_not_recorded_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _started_env(monkeypatch, tmp_path, container="cid", order=[])
    record = tmp_path / "raidar-secret-isolation.json"

    env._record_sandbox_env("task", {"B": "", "A": ""})  # noqa: SLF001
    first = record.read_text()
    record.write_text("kept")
    env._record_sandbox_env("task", {"A": "", "B": ""})  # noqa: SLF001
    assert record.read_text() == "kept"
    env._record_sandbox_env("task", {"A": ""})  # noqa: SLF001
    assert record.read_text() != "kept"
    assert json.loads(first)["sandbox_env_names"]["task"] == ["A", "B"]


def test_a_trial_project_is_named_by_trial_and_host_scoped_job_directory() -> None:
    first, second = Path("/jobs/a/trials"), Path("/jobs/b/trials")
    name = trial_project_name("b00-p0-baseline__env", first)
    assert name.startswith("b00-p0-baseline")
    assert run_scope(first) in name
    assert name != trial_project_name("b00-p0-baseline__env", second)


def test_an_exec_with_no_command_after_the_main_service_is_not_translated() -> None:
    assert engine_command(["exec", "-T"], "cid", stdin=False) is None


async def test_streamed_engine_output_is_returned_and_a_failing_command_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_exec(*argv: str, **_: Any) -> object:
        return SimpleNamespace()

    monkeypatch.setattr(env_module.asyncio, "create_subprocess_exec", fake_exec)
    env = object.__new__(RunScopedDockerEnvironment)
    env._is_windows_container = False  # noqa: SLF001
    env.session_id = "b00-p0-baseline__env-abc"
    env.environment_name = "fixture"
    monkeypatch.setattr(env, "_compose_env_vars", lambda include_os_env=True: {}, raising=False)
    codes = iter([0, 3])
    sinks: list[object] = []

    async def streamed(process: object, **kwargs: Any) -> ExecResult:
        sinks.append(kwargs["on_output"])
        return ExecResult(return_code=next(codes), stdout="line\n")

    monkeypatch.setattr(env, "_collect_streamed_output", streamed, raising=False)

    def sink(line: str) -> None:
        return None

    run = env._run_engine_command  # noqa: SLF001
    done = await run(
        ["exec", "cid", "tail"], check=True, timeout_sec=None, stdin_data=None, on_output=sink
    )
    assert (done.return_code, done.stdout, sinks) == (0, "line\n", [sink])
    with pytest.raises(RuntimeError, match="Return code: 3"):
        await run(
            ["exec", "cid", "tail"], check=True, timeout_sec=None, stdin_data=None, on_output=sink
        )


def test_a_downloaded_directory_remembers_only_its_files_for_host_edit_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/virtual/agent")
    files = {root / "a.log": (5, 10), root / "sub" / "b.log": (6, 20)}
    entries = [root / "sub", *files]
    monkeypatch.setattr(Path, "is_file", lambda self: self in files)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter(entries))
    monkeypatch.setattr(Path, "exists", lambda self: self in files)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(st_mtime_ns=files[self][0], st_size=files[self][1]),
    )
    env = object.__new__(RunScopedDockerEnvironment)
    env._synced = {}  # noqa: SLF001

    env._record_synced(root)  # noqa: SLF001

    assert env._synced == files  # noqa: SLF001
    assert env._edited_on_host(root / "a.log") is False  # noqa: SLF001
    files[root / "a.log"] = (9, 10)
    assert env._edited_on_host(root / "a.log") is True  # noqa: SLF001
