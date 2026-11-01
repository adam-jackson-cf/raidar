"""The ssh-socket transport and ``ensure``: argv, Docker context, health, data check."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.engine.target import transport as transport_module
from raidar.engine.target.config import Target, parse_targets
from raidar.engine.target.health import (
    TargetUnavailableError,
    data_check_failure,
    docker_facts,
    ensure_target,
)
from raidar.engine.target.transport import (
    TransportPaths,
    bounded_runner,
    docker_healthy,
    pid_alive,
    read_pid,
    run_command,
    spawn_detached,
    ssh_argv,
    state_dir,
    transport_down,
    transport_up,
    tunnel_running,
)

TARGETS = """
[targets.mbp]
[targets.mbp.transport]
kind = "ssh-socket"
host = "me@host"
identity = "~/.ssh/key"
remote_socket = "/run/docker.sock"

[targets.plain]
docker_context = "plain-ctx"
data_check = ["check-data"]
data_check_expect = "ok=True"
"""


def targets(tmp_path: Path) -> dict[str, Target]:
    return dict(parse_targets(TARGETS, tmp_path / "t.toml").targets)


class FakeDocker:
    """Scripted docker/data-check commands; ``healthy`` flips when the tunnel is spawned."""

    def __init__(self, *, healthy: bool = False, context_exists: bool = False) -> None:
        self.healthy = healthy
        self.context_exists = context_exists
        self.calls: list[list[str]] = []
        self.data_check: tuple[int, str] = (0, "ok=True")
        self.info = json.dumps(
            {
                "ServerVersion": "29.0",
                "OperatingSystem": "OrbStack",
                "Architecture": "aarch64",
                "NCPU": 8,
                "MemTotal": 1024,
            }
        )

    def __call__(
        self, argv: Sequence[str], timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        self.calls.append(command)
        if command[:2] == ["docker", "context"]:
            code = 0 if command[2] != "inspect" or self.context_exists else 1
            return subprocess.CompletedProcess(command, code, stdout="", stderr="")
        if "version" in command:
            return subprocess.CompletedProcess(
                command,
                0 if self.healthy else 1,
                stdout="29.0\n" if self.healthy else "",
                stderr="",
            )
        if "info" in command:
            return subprocess.CompletedProcess(
                command, 0 if self.healthy else 1, stdout=self.info, stderr=""
            )
        if command[0] == "check-data":
            code, out = self.data_check
            return subprocess.CompletedProcess(command, code, stdout=out, stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected")


class FakeTunnel:
    def __init__(self, docker: FakeDocker, *, dies: bool = False) -> None:
        self.docker = docker
        self.dies = dies
        self.spawned: list[list[str]] = []
        self.pid = 4_000_000

    def __call__(self, argv: Sequence[str], log: Path) -> FakeTunnel:
        self.spawned.append(list(argv))
        self.docker.healthy = not self.dies
        return self

    def poll(self) -> int | None:
        return 255 if self.dies else None

    def kill(self) -> None:
        self.dies = True


def test_the_ssh_forward_argv_is_detached_batch_mode_with_the_unix_socket_forward(
    tmp_path: Path,
) -> None:
    mbp = targets(tmp_path)["mbp"]
    assert mbp.transport is not None

    argv = ssh_argv(mbp.transport, tmp_path / "mbp.sock")

    assert argv[:2] == ["ssh", "-N"]
    for option in (
        "BatchMode=yes",
        "ExitOnForwardFailure=yes",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        "StreamLocalBindUnlink=yes",
        "IdentitiesOnly=yes",
    ):
        assert option in argv
    assert argv[argv.index("-i") + 1].endswith(".ssh/key")
    assert argv[-3:] == ["-L", f"{tmp_path}/mbp.sock:/run/docker.sock", "me@host"]


def test_up_spawns_the_tunnel_creates_the_context_and_waits_for_the_daemon(
    tmp_path: Path,
) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()
    tunnel = FakeTunnel(docker)

    pid = transport_up(mbp, runner=docker, spawner=tunnel, base=tmp_path, sleep=lambda _: None)

    paths = TransportPaths.of(mbp, tmp_path)
    assert pid == tunnel.pid
    assert paths.pidfile.read_text().strip() == str(tunnel.pid)
    assert len(tunnel.spawned) == 1
    assert [
        "docker",
        "context",
        "create",
        "--docker",
        f"host=unix://{paths.socket}",
        "raidar-mbp",
    ] in docker.calls
    assert docker_healthy("raidar-mbp", docker)


def test_up_updates_an_existing_context_instead_of_creating_it(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker(context_exists=True)

    transport_up(
        mbp, runner=docker, spawner=FakeTunnel(docker), base=tmp_path, sleep=lambda _: None
    )

    assert any(call[:3] == ["docker", "context", "update"] for call in docker.calls)
    assert not any(call[:3] == ["docker", "context", "create"] for call in docker.calls)


def test_up_reports_a_tunnel_that_exits(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()

    with pytest.raises(RuntimeError, match="ssh tunnel to me@host exited"):
        transport_up(
            mbp,
            runner=docker,
            spawner=FakeTunnel(docker, dies=True),
            base=tmp_path,
            sleep=lambda _: None,
        )


def test_up_does_not_leave_a_tunnel_behind_when_the_context_cannot_be_set(
    tmp_path: Path,
) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()
    tunnel = FakeTunnel(docker)
    plain_run = docker.__call__

    def failing_context(
        argv: Sequence[str], timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if list(argv)[:3] == ["docker", "context", "create"]:
            return subprocess.CompletedProcess(list(argv), 1, stdout="", stderr="denied")
        return plain_run(argv, timeout)

    with pytest.raises(RuntimeError, match="docker context setup failed"):
        transport_up(
            mbp, runner=failing_context, spawner=tunnel, base=tmp_path, sleep=lambda _: None
        )

    assert tunnel.spawned == []


def test_the_state_directory_is_created_private(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()
    base = tmp_path / "run"

    transport_up(mbp, runner=docker, spawner=FakeTunnel(docker), base=base, sleep=lambda _: None)

    assert base.stat().st_mode & 0o777 == 0o700


def test_up_gives_up_when_the_daemon_never_answers(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()

    class Silent(FakeTunnel):
        def __call__(self, argv: Sequence[str], log: Path) -> FakeTunnel:
            self.spawned.append(list(argv))
            return self

    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(RuntimeError, match="did not answer"):
        transport_up(
            mbp,
            runner=docker,
            spawner=Silent(docker),
            base=tmp_path,
            timeout_s=1.0,
            sleep=sleep,
            clock=lambda: now[0],
        )


@pytest.mark.parametrize("probe_seconds", [0.1, 6.0, 30.0])
def test_startup_bounds_slow_probes_and_cleans_up_the_failed_tunnel(
    tmp_path: Path, probe_seconds: float
) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()
    now = [0.0]
    timeouts: list[float] = []

    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        assert timeout is not None
        if "version" in argv:
            timeouts.append(timeout)
            now[0] += min(probe_seconds, timeout)
        return docker(argv, timeout)

    class Silent(FakeTunnel):
        def __call__(self, argv: Sequence[str], log: Path) -> FakeTunnel:
            self.spawned.append(list(argv))
            return self

    tunnel = Silent(docker)
    with pytest.raises((RuntimeError, TimeoutError)):
        transport_up(
            mbp,
            runner=runner,
            spawner=tunnel,
            base=tmp_path,
            timeout_s=4.0,
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            clock=lambda: now[0],
        )

    assert now[0] == pytest.approx(4.0)
    assert timeouts
    assert max(timeouts) <= 4.0
    assert tunnel.dies
    assert not TransportPaths.of(mbp, tmp_path).pidfile.exists()


def test_ensure_is_a_no_op_for_a_healthy_transport_target(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker(healthy=True)
    tunnel = FakeTunnel(docker)

    ensure_target(mbp, runner=docker, spawner=tunnel, base=tmp_path)

    assert tunnel.spawned == []


def test_ensure_restarts_an_unhealthy_transport_target(tmp_path: Path) -> None:
    mbp = targets(tmp_path)["mbp"]
    docker = FakeDocker()
    tunnel = FakeTunnel(docker)

    ensure_target(mbp, runner=docker, spawner=tunnel, base=tmp_path, sleep=lambda _: None)

    assert len(tunnel.spawned) == 1


@pytest.mark.parametrize("phase", ["version", "inspect", "create", "check-data"])
def test_ensure_bounds_all_subprocess_phases_by_the_shared_reconnect_budget(
    tmp_path: Path, phase: str
) -> None:
    items = targets(tmp_path)
    chosen = items["plain"] if phase == "check-data" else items["mbp"]
    docker = FakeDocker(healthy=phase == "check-data")
    now = [0.0]
    budgets: list[float] = []

    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        assert timeout is not None
        budgets.append(timeout)
        if phase in argv:
            now[0] += timeout
            return subprocess.CompletedProcess(list(argv), 127, stdout="", stderr="timeout")
        return docker(argv, timeout)

    with pytest.raises(TargetUnavailableError):
        ensure_target(
            chosen,
            runner=runner,
            spawner=FakeTunnel(docker),
            base=tmp_path,
            timeout_s=0.2,
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            clock=lambda: now[0],
        )

    assert now[0] <= 0.2
    assert budgets
    assert max(budgets) <= 0.2


def test_ensure_wraps_a_failed_transport_and_an_unreachable_plain_target(tmp_path: Path) -> None:
    items = targets(tmp_path)
    docker = FakeDocker()

    with pytest.raises(TargetUnavailableError, match="transport to me@host failed"):
        ensure_target(
            items["mbp"],
            runner=docker,
            spawner=FakeTunnel(docker, dies=True),
            base=tmp_path,
            sleep=lambda _: None,
        )
    with pytest.raises(TargetUnavailableError, match="'plain-ctx'.*unreachable"):
        ensure_target(items["plain"], runner=docker)


def test_the_data_check_needs_exit_zero_and_the_expected_output(tmp_path: Path) -> None:
    plain = targets(tmp_path)["plain"]
    docker = FakeDocker(healthy=True)

    assert data_check_failure(plain, docker) is None
    docker.data_check = (0, "ok=False")
    assert "did not report 'ok=True'" in (data_check_failure(plain, docker) or "")
    docker.data_check = (3, "boom")
    assert "exited 3" in (data_check_failure(plain, docker) or "")
    with pytest.raises(TargetUnavailableError, match="exited 3"):
        ensure_target(plain, runner=docker)


def test_docker_facts_read_the_daemon_and_are_none_when_unreachable(tmp_path: Path) -> None:
    plain = targets(tmp_path)["plain"]
    docker = FakeDocker(healthy=True)

    facts = docker_facts(plain, docker)

    assert facts is not None
    assert facts.as_record() == {
        "server_version": "29.0",
        "os": "OrbStack",
        "arch": "aarch64",
        "ncpu": 8,
        "mem_total": 1024,
    }
    docker.healthy = False
    assert docker_facts(plain, docker) is None


def test_status_prints_facts_and_exits_non_zero_when_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "targets.toml").write_text(TARGETS)
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "targets.toml"))
    monkeypatch.setenv("RAIDAR_STATE_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    docker = FakeDocker(healthy=True)
    monkeypatch.setattr("raidar.cli.target.docker_facts", lambda t: docker_facts(t, docker))
    monkeypatch.setattr(
        "raidar.cli.target.data_check_failure", lambda t: data_check_failure(t, docker)
    )

    up = CliRunner().invoke(main, ["target", "status", "--target", "plain"])
    docker.healthy = False
    down = CliRunner().invoke(main, ["target", "status", "--target", "mbp"])

    report = json.loads(up.output)
    assert up.exit_code == 0, up.output
    assert (report["docker"]["server_version"], report["docker"]["ncpu"]) == ("29.0", 8)
    assert report["healthy"] is True
    assert down.exit_code == 1
    assert json.loads(down.output)["transport"]["host"] == "me@host"
    assert json.loads(down.output)["transport"]["alive"] is False


# ---- subprocess seams: success, non-zero, timeout, malformed output ---------------------


def test_run_command_reports_a_missing_binary_or_a_timeout_as_a_failed_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome: list[Exception | subprocess.CompletedProcess[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        result = outcome.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(transport_module.subprocess, "run", run)
    ok = subprocess.CompletedProcess(["x"], 0, stdout="fine", stderr="")
    outcome[:] = [ok, FileNotFoundError("no ssh"), subprocess.TimeoutExpired(["x"], 1)]

    assert run_command(["x"]).stdout == "fine"
    missing = run_command(["x"])
    timed_out = run_command(["x"], timeout=1)
    assert (missing.returncode, timed_out.returncode) == (127, 127)
    assert "no ssh" in missing.stderr
    assert "timed out" in timed_out.stderr


def test_the_tunnel_is_spawned_detached_into_its_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def popen(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        seen.append((argv, kwargs))
        return SimpleNamespace(pid=9)

    monkeypatch.setattr(transport_module.subprocess, "Popen", popen)
    log = tmp_path / "state" / "mbp.log"

    process = spawn_detached(["ssh", "-N"], log)

    assert process.pid == 9
    assert log.parent.is_dir()
    argv, kwargs = seen[0]
    assert argv == ["ssh", "-N"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_the_state_dir_is_the_override_else_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RAIDAR_STATE_DIR", str(tmp_path / "run"))
    assert state_dir() == tmp_path / "run"
    monkeypatch.delenv("RAIDAR_STATE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state_dir() == tmp_path / ".raidar" / "run"


def test_the_ssh_forward_without_an_identity_uses_the_agent(tmp_path: Path) -> None:
    text = TARGETS.replace('identity = "~/.ssh/key"\n', "")
    mbp = dict(parse_targets(text, tmp_path / "t.toml").targets)["mbp"]
    assert mbp.transport is not None

    argv = ssh_argv(mbp.transport, tmp_path / "s.sock")

    assert "-i" not in argv
    assert "IdentitiesOnly=yes" not in argv


def test_a_shared_deadline_bounds_every_subprocess(tmp_path: Path) -> None:
    now = [0.0]
    seen: list[float | None] = []

    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        seen.append(timeout)
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    bounded = bounded_runner(runner, deadline=10.0, clock=lambda: now[0])
    bounded(["a"], None)
    bounded(["b"], 3.0)
    bounded(["c"], 99.0)
    now[0] = 10.0

    assert seen == [10.0, 3.0, 10.0]
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        bounded(["d"], 1.0)


def test_a_transport_less_target_cannot_be_brought_up(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="has no transport"):
        transport_up(targets(tmp_path)["plain"], base=tmp_path)


# ---- pid and tunnel liveness (os seams injected) ---------------------------------------


class FakeOs:
    """``os`` as the transport sees it: a pid that is alive until a signal ends it."""

    WNOHANG = os.WNOHANG
    environ = os.environ

    def __init__(self, *, alive: bool = True, dies_on: int | None = signal.SIGTERM) -> None:
        self.alive = alive
        self.dies_on = dies_on
        self.signals: list[int] = []
        self.exited_child: int | None = None
        self.kill_error: Exception | None = None

    def waitpid(self, pid: int, flags: int) -> tuple[int, int]:
        if self.exited_child == pid:
            return pid, 0
        raise ChildProcessError

    def kill(self, pid: int, sig: int) -> None:
        self.signals.append(sig)
        if self.kill_error:
            raise self.kill_error
        if sig == 0:
            if not self.alive:
                raise ProcessLookupError
            return
        if sig in (signal.SIGKILL, self.dies_on):
            self.alive = False


@pytest.mark.parametrize(
    ("pid", "setup", "alive"),
    [
        (None, {}, False),
        (5, {"alive": True}, True),
        (5, {"alive": False}, False),
    ],
)
def test_a_pid_is_alive_only_while_it_exists(
    monkeypatch: pytest.MonkeyPatch, pid: int | None, setup: dict[str, Any], alive: bool
) -> None:
    monkeypatch.setattr(transport_module, "os", FakeOs(**setup))

    assert pid_alive(pid) is alive


def test_an_exited_child_is_reaped_and_a_foreign_process_still_counts_as_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeOs()
    fake.exited_child = 5
    monkeypatch.setattr(transport_module, "os", fake)
    assert pid_alive(5) is False
    fake.exited_child = None
    fake.kill_error = PermissionError()
    assert pid_alive(5) is True


def test_a_tunnel_is_running_only_for_this_targets_ssh_forward(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(transport_module, "os", FakeOs())
    paths = TransportPaths.of(targets(tmp_path)["mbp"], tmp_path)
    shown = [subprocess.CompletedProcess([], 0, stdout=f"ssh -L {paths.socket}:/s", stderr="")]

    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        return shown[0]

    assert tunnel_running(paths, runner) is False, "no pidfile"
    paths.pidfile.parent.mkdir(parents=True, exist_ok=True)
    paths.pidfile.write_text("garbage")
    assert read_pid(paths) is None
    paths.pidfile.write_text("77\n")
    assert read_pid(paths) == 77
    assert tunnel_running(paths, runner) is True
    shown[0] = subprocess.CompletedProcess([], 0, stdout="vim other", stderr="")
    assert tunnel_running(paths, runner) is False, "a recycled pid"
    shown[0] = subprocess.CompletedProcess([], 1, stdout=str(paths.socket), stderr="")
    assert tunnel_running(paths, runner) is False, "ps failed"


def _running_tunnel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeOs
) -> tuple[Target, Callable[..., Any]]:
    monkeypatch.setattr(transport_module, "os", fake)
    mbp = targets(tmp_path)["mbp"]
    paths = TransportPaths.of(mbp, tmp_path)
    paths.pidfile.write_text("77\n")
    paths.socket.write_text("")

    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=str(paths.socket), stderr="")

    return mbp, runner


def test_down_terminates_a_running_tunnel_and_forgets_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeOs(dies_on=signal.SIGTERM)
    mbp, runner = _running_tunnel(monkeypatch, tmp_path, fake)
    paths = TransportPaths.of(mbp, tmp_path)

    assert transport_down(mbp, base=tmp_path, runner=runner, sleep=lambda _s: None) is True

    assert signal.SIGTERM in fake.signals
    assert signal.SIGKILL not in fake.signals
    assert not paths.pidfile.exists()
    assert not paths.socket.exists()


def test_down_kills_a_tunnel_that_ignores_terminate_once_the_wait_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeOs(dies_on=None)
    mbp, runner = _running_tunnel(monkeypatch, tmp_path, fake)
    now = [0.0]

    stopped = transport_down(
        mbp,
        base=tmp_path,
        runner=runner,
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
        deadline=2.0,
    )

    assert stopped is True
    assert fake.signals[-1] == signal.SIGKILL
    assert now[0] <= 2.0 + 1e-9, "the shared deadline bounds the wait"


def test_down_clears_stale_files_and_reports_nothing_was_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeOs(alive=False)
    mbp, runner = _running_tunnel(monkeypatch, tmp_path, fake)
    paths = TransportPaths.of(mbp, tmp_path)

    assert transport_down(mbp, base=tmp_path, runner=runner) is False

    assert fake.signals == [0]
    assert not paths.pidfile.exists()
    assert not paths.socket.exists()


def test_down_tolerates_a_tunnel_that_vanishes_before_the_terminate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeOs()
    mbp, runner = _running_tunnel(monkeypatch, tmp_path, fake)
    real_kill = fake.kill

    def kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            raise ProcessLookupError
        real_kill(pid, sig)

    monkeypatch.setattr(fake, "kill", kill)

    assert transport_down(mbp, base=tmp_path, runner=runner) is True


# ---- health: malformed output and deadlines --------------------------------------------


@pytest.mark.parametrize(
    "info",
    [
        "not json",
        "{}",
        json.dumps({"ServerVersion": "1", "OperatingSystem": "o", "Architecture": "a"}),
        json.dumps(
            {
                "ServerVersion": "1",
                "OperatingSystem": "o",
                "Architecture": "a",
                "NCPU": "many",
                "MemTotal": 1,
            }
        ),
        "null",
    ],
)
def test_docker_facts_are_none_for_malformed_daemon_output(tmp_path: Path, info: str) -> None:
    docker = FakeDocker(healthy=True)
    docker.info = info

    assert docker_facts(targets(tmp_path)["plain"], docker) is None


def test_a_target_without_a_data_check_passes_it(tmp_path: Path) -> None:
    assert data_check_failure(targets(tmp_path)["mbp"], FakeDocker()) is None


def test_a_probe_past_the_deadline_makes_the_target_unavailable(tmp_path: Path) -> None:
    def runner(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        raise TimeoutError("target operation deadline exceeded")

    with pytest.raises(TargetUnavailableError, match="target 'plain': .*deadline exceeded"):
        ensure_target(targets(tmp_path)["plain"], runner=runner)


# ---- ``raidar target`` commands ---------------------------------------------------------


@pytest.fixture
def target_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    (tmp_path / "targets.toml").write_text(TARGETS)
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "targets.toml"))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    return CliRunner()


def _raise(error: Exception) -> Callable[..., Any]:
    def fail(*_: Any, **__: Any) -> Any:
        raise error

    return fail


@pytest.mark.parametrize(
    ("name", "outcome", "exit_code", "text"),
    [
        ("up", 41, 0, "mbp: up (ssh pid 41, context raidar-mbp)"),
        ("up", RuntimeError("ssh tunnel exited"), 1, "ssh tunnel exited"),
        ("up", OSError("no ssh"), 1, "no ssh"),
        ("down", True, 0, "mbp: stopped"),
        ("down", False, 0, "mbp: was not running"),
    ],
)
def test_up_and_down_report_what_happened_to_the_transport(
    monkeypatch: pytest.MonkeyPatch,
    target_cli: CliRunner,
    name: str,
    outcome: object,
    exit_code: int,
    text: str,
) -> None:
    action = _raise(outcome) if isinstance(outcome, Exception) else (lambda *_a, **_k: outcome)
    monkeypatch.setattr(f"raidar.cli.target.transport_{name}", action)

    result = target_cli.invoke(main, ["target", name, "--target", "mbp"])

    assert result.exit_code == exit_code, result.output
    assert text in result.output


@pytest.mark.parametrize("name", ["up", "down"])
def test_a_target_without_a_transport_has_nothing_to_start_or_stop(
    target_cli: CliRunner, name: str
) -> None:
    result = target_cli.invoke(main, ["target", name, "--target", "plain"])

    assert result.exit_code != 0
    assert "has no transport" in result.output


def test_ensure_reports_ok_or_why_the_target_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, target_cli: CliRunner
) -> None:
    monkeypatch.setattr("raidar.cli.target.ensure_target", lambda chosen: None)
    ok = target_cli.invoke(main, ["target", "ensure", "--target", "plain"])
    monkeypatch.setattr(
        "raidar.cli.target.ensure_target", _raise(TargetUnavailableError("daemon down"))
    )
    down = target_cli.invoke(main, ["target", "ensure", "--target", "plain"])

    assert (ok.exit_code, "plain: ok (context plain-ctx)" in ok.output) == (0, True)
    assert down.exit_code != 0
    assert "daemon down" in down.output


def test_an_unknown_target_is_a_command_error(target_cli: CliRunner) -> None:
    for argv in (["status", "--target", "nope"], ["ensure", "--target", "nope"]):
        result = target_cli.invoke(main, ["target", *argv])
        assert result.exit_code != 0
        assert "unknown target 'nope'" in result.output


def test_a_broken_targets_file_is_a_list_error(tmp_path: Path, target_cli: CliRunner) -> None:
    (tmp_path / "targets.toml").write_text("[targets.a\n")

    result = target_cli.invoke(main, ["target", "list"])

    assert result.exit_code != 0
    assert "invalid targets file" in result.output


def test_status_exits_non_zero_when_the_data_check_fails(
    monkeypatch: pytest.MonkeyPatch, target_cli: CliRunner
) -> None:
    docker = FakeDocker(healthy=True)
    docker.data_check = (3, "boom")
    monkeypatch.setattr("raidar.cli.target.docker_facts", lambda t: docker_facts(t, docker))
    monkeypatch.setattr(
        "raidar.cli.target.data_check_failure", lambda t: data_check_failure(t, docker)
    )

    result = target_cli.invoke(main, ["target", "status", "--target", "plain"])

    report = json.loads(result.output)
    assert result.exit_code == 1
    assert (report["reachable"], report["healthy"]) == (True, False)
    assert "exited 3" in report["problem"]


@pytest.mark.parametrize(
    ("argv", "removed", "text"),
    [
        ([], ["b00-p0-baseline__env-0123456789"], "removed: b00-p0-baseline__env-0123456789"),
        (["--dry-run"], ["a__env-abcdefabcd"], "would remove: a__env-abcdefabcd"),
        ([], [], "0 stale trial environment(s) removed."),
    ],
)
def test_prune_reports_what_it_removed_or_would_remove(
    monkeypatch: pytest.MonkeyPatch,
    target_cli: CliRunner,
    argv: list[str],
    removed: list[str],
    text: str,
) -> None:
    seen: list[tuple[object, str, bool]] = []

    def cleanup(job: object, *, docker_context: str, dry_run: bool) -> list[str]:
        seen.append((job, docker_context, dry_run))
        return removed

    monkeypatch.setattr("raidar.cli.target.docker_healthy", lambda context: True)
    monkeypatch.setattr("raidar.cli.target.cleanup_trial_projects", cleanup)

    result = target_cli.invoke(main, ["target", "prune", "--target", "plain", *argv])

    assert result.exit_code == 0, result.output
    assert text in result.output
    assert seen == [(None, "plain-ctx", "--dry-run" in argv)]


@pytest.mark.parametrize(
    ("healthy", "failure", "text"),
    [
        (False, None, "unreachable; run `raidar target ensure` first"),
        (True, RuntimeError("docker compose down failed for x"), "down failed for x"),
        (True, subprocess.TimeoutExpired(["docker"], 60), "timed out"),
        (True, OSError("transport lost"), "transport lost"),
    ],
)
def test_prune_refuses_an_unreachable_target_and_reports_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    target_cli: CliRunner,
    healthy: bool,
    failure: Exception | None,
    text: str,
) -> None:
    monkeypatch.setattr("raidar.cli.target.docker_healthy", lambda context: healthy)
    monkeypatch.setattr(
        "raidar.cli.target.cleanup_trial_projects", _raise(failure or AssertionError("no docker"))
    )

    result = target_cli.invoke(main, ["target", "prune", "--target", "plain"])

    assert result.exit_code != 0
    assert text in result.output
