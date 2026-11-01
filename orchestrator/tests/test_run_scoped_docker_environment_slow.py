"""Container discovery deadlines with injected processes and real-clock waits."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from harbor.environments.base import ExecResult

from raidar.engine.trial import environment as env_module
from tests.test_run_scoped_docker_environment import _bare_env, _resolvable_env

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("stop", ["deadline", "cancel", "lookup_bound"])
async def test_blocked_discovery_is_bounded_and_reaps_its_process(
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    terminated = False
    reaped = False

    class BlockedProcess:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, None]:
            started.set()
            await asyncio.Event().wait()
            return b"", None

        def terminate(self) -> None:
            nonlocal terminated
            terminated = True
            self.returncode = -15

        async def wait(self) -> int:
            nonlocal reaped
            reaped = True
            return -15

    async def spawn(*_: Any, **__: Any) -> BlockedProcess:
        return BlockedProcess()

    env, _ = _resolvable_env(monkeypatch, tmp_path, b"")
    monkeypatch.setattr(env_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        env_module, "CONTAINER_LOOKUP_TIMEOUT_S", 0.01 if stop == "lookup_bound" else 10
    )
    task = asyncio.create_task(
        env._run_docker_compose_command(  # noqa: SLF001
            ["exec", "main", "true"],
            timeout_sec=0.01 if stop == "deadline" else None,
        )
    )
    await started.wait()
    if stop == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="discovery timed out"):
            await task
    assert terminated
    assert reaped


async def test_container_lookup_consumes_the_execution_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = _bare_env("cid", tmp_path)
    observed: list[float] = []

    async def resolve() -> str:
        await asyncio.sleep(0.01)
        return "cid"

    async def execute(argv: list[str], *, timeout_sec: float, **_: Any) -> ExecResult:
        observed.append(timeout_sec)
        return ExecResult(return_code=0)

    monkeypatch.setattr(env, "_resolve_main_container", resolve)
    monkeypatch.setattr(env, "_run_engine_command", execute)
    await env._run_docker_compose_command(["exec", "main", "true"], timeout_sec=1)  # noqa: SLF001
    assert len(observed) == 1
    assert 0 < observed[0] < 0.995
