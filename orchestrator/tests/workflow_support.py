"""Shared scripted-agent runtime and fixtures for workflow behavior scenarios."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.models.agent.context import AgentContext

from tests.native_support import CodexNative
from tests.primitive_support import NoTransfers

CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
PASSING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
FAILING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 6\n"


class HarborEnvironmentDouble(BaseEnvironment):
    """A Harbor environment with no trial behind it; a test overrides what its scenario uses."""

    mounted: ClassVar[bool] = True
    """Whether ``/logs/agent`` is mounted, so Harbor needs no download of it."""

    def __init__(self) -> None:
        """Skip Harbor's constructor, which needs a task, a trial directory and a definition."""

    @staticmethod
    def type() -> str:
        return "test-double"

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(mounted=self.mounted)

    def _validate_definition(self) -> None:
        return None

    async def start(self, force_build: bool) -> None:
        raise AssertionError("a test double never starts")

    async def stop(self, delete: bool) -> None:
        raise AssertionError("a test double never stops")

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        raise AssertionError(f"unexpected upload of {source_path}")

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        raise AssertionError(f"unexpected upload of {source_dir}")

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        raise AssertionError(f"unexpected download of {source_path}")

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        raise AssertionError(f"unexpected download of {source_dir}")

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        raise AssertionError(f"unexpected command {command}")


AgentTurn = Callable[[Path, str], str | None]
"""A scripted turn: edits and commits in the workspace; a returned string is the turn's reply."""


class LocalEnvironment(NoTransfers):
    """The container as the host's shell: commands run in a subprocess, uploads are copies."""

    capabilities = SimpleNamespace(mounted=True)

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    async def exec(self, command: str, user: str | int | None = None, **_: Any) -> Any:
        process = await asyncio.create_subprocess_shell(
            command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = await asyncio.wait_for(process.communicate(), timeout=120)
        return SimpleNamespace(
            return_code=process.returncode, stdout=out.decode(), stderr=err.decode()
        )

    async def upload_file(self, source: Path | str, target: str) -> None:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


class ScriptedAgent(CodexNative):
    """A native agent whose turns are scripted edits and commits in the workspace."""

    turns: ClassVar[list[AgentTurn]] = []
    prompts: ClassVar[list[tuple[bool, str]]] = []
    workspace: ClassVar[Path]

    @staticmethod
    def name() -> str:
        return "scripted"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: Any) -> None:
        return None

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, False, context)

    async def resume(self, instruction: str, environment: Any, context: AgentContext) -> None:
        self._turn(instruction, True, context)

    def _turn(self, instruction: str, resumed: bool, context: AgentContext) -> None:
        cls = type(self)
        cls.prompts.append((resumed, instruction))
        turns = len(cls.prompts)  # the session's cumulative totals, as codex reports them
        context.n_input_tokens, context.n_cache_tokens = 1000 * turns, 600 * turns
        context.n_output_tokens, context.cost_usd = 50 * turns, None
        (self.logs_dir / "trajectory.json").write_text(json.dumps({"session_id": "session-1"}))
        reply = cls.turns.pop(0)(cls.workspace, instruction)
        steps = (
            []
            if reply is None
            else [{"step_id": turns, "timestamp": f"t{turns}", "source": "agent", "message": reply}]
        )
        trajectory = {"session_id": "session-1", "steps": steps}
        (self.logs_dir / "trajectory.json").write_text(json.dumps(trajectory))
        output = (
            []
            if reply is None
            else [{"type": "item.completed", "item": {"type": "agent_message", "text": reply}}]
        )
        (self.logs_dir / "codex.txt").write_text(
            "\n".join(json.dumps(item) for item in output) + "\n"
        )


@pytest.fixture
def agent_env() -> dict[str, str]:
    """The agent's and the container's environment: the test's own."""
    return dict(os.environ)


def write(workspace: Path, **files: str) -> None:
    for name, text in files.items():
        (workspace / f"{name}.py").write_text(text)


def commit(workspace: Path, env: dict[str, str], *flags: str) -> int:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, env=env, timeout=120)
    message = ["-m", "feat: add"]
    done = subprocess.run(
        ["git", "commit", "-q", *message, *flags], cwd=workspace, env=env, timeout=120
    )
    return done.returncode
