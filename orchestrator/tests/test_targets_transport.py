"""The ssh-socket transport and ``ensure``: argv, Docker context, health, data check."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.engine.targets.config import Target, parse_targets
from raidar.engine.targets.health import (
    TargetUnavailableError,
    data_check_failure,
    docker_facts,
    ensure_target,
)
from raidar.engine.targets.transport import (
    TransportPaths,
    docker_healthy,
    ssh_argv,
    transport_up,
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

    with pytest.raises(RuntimeError, match="did not answer"):
        transport_up(
            mbp,
            runner=docker,
            spawner=Silent(docker),
            base=tmp_path,
            timeout_s=1.0,
            sleep=lambda _: None,
        )


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
