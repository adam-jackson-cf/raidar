"""Target health: daemon facts, the configured data check, and an idempotent ``ensure``."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from raidar.engine.targets.config import Target, TargetError
from raidar.engine.targets.transport import (
    Runner,
    Spawner,
    docker_healthy,
    run_command,
    spawn_detached,
    transport_up,
)

DATA_CHECK_TIMEOUT_S = 30.0
INFO_TIMEOUT_S = 20.0


class TargetUnavailableError(TargetError):
    """The target's daemon is unreachable or its data check fails."""


@dataclass(frozen=True)
class DockerFacts:
    server_version: str
    os: str
    arch: str
    ncpu: int
    mem_total: int

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


def docker_facts(target: Target, runner: Runner = run_command) -> DockerFacts | None:
    """What ``docker info`` reports about the target's daemon; None when unreachable."""
    done = runner(
        ["docker", "--context", target.docker_context, "info", "--format", "{{json .}}"],
        INFO_TIMEOUT_S,
    )
    if done.returncode != 0:
        return None
    try:
        info = json.loads(done.stdout)
        return DockerFacts(
            server_version=str(info["ServerVersion"]),
            os=str(info["OperatingSystem"]),
            arch=str(info["Architecture"]),
            ncpu=int(info["NCPU"]),
            mem_total=int(info["MemTotal"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def data_check_failure(target: Target, runner: Runner = run_command) -> str | None:
    """Why the target's data check fails, or None when it passes (or none is set)."""
    if not target.data_check:
        return None
    done = runner(list(target.data_check), DATA_CHECK_TIMEOUT_S)
    if done.returncode != 0:
        output = (done.stdout + done.stderr).strip()
        return f"data check {' '.join(target.data_check)} exited {done.returncode}: {output}"
    expect = target.data_check_expect
    if expect and expect not in done.stdout:
        shown = done.stdout.strip()
        return f"data check {' '.join(target.data_check)} did not report {expect!r}: {shown}"
    return None


def ensure_target(
    target: Target,
    *,
    runner: Runner = run_command,
    spawner: Spawner = spawn_detached,
    base: Path | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Healthy: no-op. Unhealthy transport target: restart the tunnel. Then the data check."""
    if not docker_healthy(target.docker_context, runner):
        if target.transport is None:
            raise TargetUnavailableError(
                f"docker context {target.docker_context!r} (target {target.name!r}) is unreachable"
            )
        extra: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
        try:
            transport_up(target, runner=runner, spawner=spawner, base=base, **extra)
        except (RuntimeError, OSError) as error:
            raise TargetUnavailableError(
                f"target {target.name!r}: transport to {target.transport.host} failed: {error}"
            ) from error
    failure = data_check_failure(target, runner)
    if failure is not None:
        raise TargetUnavailableError(f"target {target.name!r}: {failure}")
