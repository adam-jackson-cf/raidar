"""The ``ssh-socket`` transport: a detached ``ssh -L`` forward of a remote Docker socket.

``up`` spawns the tunnel (own session, log and pidfile under the state dir),
creates or updates the Docker context ``raidar-<name>`` that points at the
local end, and waits until the daemon answers. ``down`` stops the tunnel.
The tunnel outlives the command that started it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from raidar.engine.target.config import Target, Transport

ENV_STATE_DIR = "RAIDAR_STATE_DIR"
UP_TIMEOUT_S = 20.0
HEALTH_TIMEOUT_S = 10.0
_POLL_S = 0.5
_STOP_WAIT_S = 5.0

Runner = Callable[[Sequence[str], float | None], subprocess.CompletedProcess[str]]


def bounded_runner(runner: Runner, deadline: float, clock: Callable[[], float]) -> Runner:
    """Share a wall-clock deadline across subprocesses, including their execution time."""

    def run(argv: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("target operation deadline exceeded")
        return runner(argv, remaining if timeout is None else min(timeout, remaining))

    return run


def run_command(
    argv: Sequence[str], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` capturing output; a missing binary or a timeout is a failed process."""
    try:
        return subprocess.run(  # noqa: S603 - argv list, no shell
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(list(argv), 127, stdout="", stderr=str(error))


class SpawnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def kill(self) -> None: ...


Spawner = Callable[[Sequence[str], Path], SpawnedProcess]


def spawn_detached(argv: Sequence[str], log: Path) -> SpawnedProcess:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as sink:
        return subprocess.Popen(  # noqa: S603 - argv list, no shell
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=True,
        )


def state_dir() -> Path:
    override = os.environ.get(ENV_STATE_DIR)
    return Path(override).expanduser() if override else Path.home() / ".raidar" / "run"


@dataclass(frozen=True)
class TransportPaths:
    socket: Path
    pidfile: Path
    log: Path

    @classmethod
    def of(cls, target: Target, base: Path | None = None) -> TransportPaths:
        base = base or state_dir()
        return cls(
            base / f"{target.name}.sock", base / f"{target.name}.pid", base / f"{target.name}.log"
        )


def ssh_argv(transport: Transport, socket: Path) -> list[str]:
    argv = [
        "ssh",
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StreamLocalBindUnlink=yes",
    ]
    if transport.identity:
        argv += ["-o", "IdentitiesOnly=yes", "-i", str(Path(transport.identity).expanduser())]
    return [*argv, "-L", f"{socket}:{transport.remote_socket}", transport.host]


def read_pid(paths: TransportPaths) -> int | None:
    try:
        return int(paths.pidfile.read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:  # our own exited child: reap it
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def tunnel_running(paths: TransportPaths, runner: Runner = run_command) -> bool:
    """The pidfile's process is alive and is this target's ssh forward (pids get reused)."""
    pid = read_pid(paths)
    if not pid_alive(pid):
        return False
    shown = runner(["ps", "-p", str(pid), "-o", "command="], HEALTH_TIMEOUT_S)
    return shown.returncode == 0 and str(paths.socket) in shown.stdout


def docker_healthy(context: str, runner: Runner = run_command) -> bool:
    done = runner(
        ["docker", "--context", context, "version", "--format", "{{.Server.Version}}"],
        HEALTH_TIMEOUT_S,
    )
    return done.returncode == 0 and bool(done.stdout.strip())


def ensure_context(target: Target, paths: TransportPaths, runner: Runner = run_command) -> None:
    """Create or update the Docker context ``raidar-<name>`` to the tunnel's local socket."""
    endpoint = f"host=unix://{paths.socket}"
    exists = runner(["docker", "context", "inspect", target.docker_context], HEALTH_TIMEOUT_S)
    if exists.returncode == 0:
        argv = ["docker", "context", "update", "--docker", endpoint, target.docker_context]
    else:
        argv = ["docker", "context", "create", "--docker", endpoint, target.docker_context]
    done = runner(argv, HEALTH_TIMEOUT_S)
    if done.returncode != 0:
        raise RuntimeError(f"docker context setup failed: {done.stderr.strip()}")


def transport_up(
    target: Target,
    *,
    runner: Runner = run_command,
    spawner: Spawner = spawn_detached,
    base: Path | None = None,
    timeout_s: float = UP_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> int:
    """Start (or restart) the tunnel and wait until the daemon answers; the tunnel's pid."""
    transport = target.transport
    if transport is None:
        raise ValueError(f"target {target.name!r} has no transport")
    deadline = min(clock() + timeout_s, deadline) if deadline is not None else clock() + timeout_s
    runner = bounded_runner(runner, deadline, clock)
    paths = TransportPaths.of(target, base)
    transport_down(target, base=base, sleep=sleep, runner=runner, deadline=deadline, clock=clock)
    paths.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ensure_context(target, paths, runner)
    process = spawner(ssh_argv(transport, paths.socket), paths.log)
    paths.pidfile.write_text(f"{process.pid}\n")
    try:
        while clock() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"ssh tunnel to {transport.host} exited ({process.poll()}); see {paths.log}"
                )
            if docker_healthy(target.docker_context, runner):
                return process.pid
            sleep(min(_POLL_S, max(0.0, deadline - clock())))
        raise RuntimeError(
            f"docker did not answer on {target.docker_context} within {timeout_s:.0f}s; "
            f"see {paths.log}"
        )
    except BaseException:
        # This child is ours: cleanup needs no further subprocess or timeout budget.
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        paths.pidfile.unlink(missing_ok=True)
        paths.socket.unlink(missing_ok=True)
        raise


def transport_down(
    target: Target,
    *,
    base: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
    runner: Runner = run_command,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Stop the tunnel; whether one was running."""
    paths = TransportPaths.of(target, base)
    pid = read_pid(paths)
    was_running = tunnel_running(paths, runner)
    if pid is not None and was_running:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
            stop_deadline = clock() + _STOP_WAIT_S
            if deadline is not None:
                stop_deadline = min(stop_deadline, deadline)
            while pid_alive(pid) and clock() < stop_deadline:
                sleep(min(0.1, max(0.0, stop_deadline - clock())))
            if pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
    paths.pidfile.unlink(missing_ok=True)
    paths.socket.unlink(missing_ok=True)
    return was_running
