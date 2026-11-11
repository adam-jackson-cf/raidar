"""Container discovery against a real blocked process: the deadline ends and reaps it.

The deadline, cancellation and lookup-bound decisions are fast tests in
``test_run_scoped_docker_environment.py``; this proves a real child that never answers is
terminated and reaped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.test_run_scoped_docker_environment import _resolvable_env

pytestmark = pytest.mark.slow


async def test_a_real_discovery_process_that_never_answers_is_terminated_and_reaped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_spawn = asyncio.create_subprocess_exec
    env, _ = _resolvable_env(monkeypatch, tmp_path, b"")
    spawned: list[asyncio.subprocess.Process] = []

    async def spawn_blocker(*_: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_spawn(
            "sleep", "60", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_blocker)

    with pytest.raises(RuntimeError, match="discovery timed out"):
        await env._run_docker_compose_command(  # noqa: SLF001
            ["exec", "main", "true"], timeout_sec=0.2
        )

    assert spawned
    assert spawned[0].returncode is not None, "the blocked child must be reaped, not leaked"
