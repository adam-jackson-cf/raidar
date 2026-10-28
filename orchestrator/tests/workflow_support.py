"""Shared scripted-agent runtime and fixtures for workflow behavior scenarios."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import coverage
import pytest
from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext

from raidar.subject.workflow import verify
from tests.native_support import CodexNative

CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
PASSING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
FAILING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 6\n"

AgentTurn = Callable[[Path, str], str | None]
"""A scripted turn: edits and commits in the workspace; a returned string is the turn's reply."""


class LocalEnvironment:
    """The container as the host's shell: commands run in a subprocess, uploads are copies."""

    capabilities = SimpleNamespace(mounted=True)

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    async def exec(self, command: str, user: str | None = None, **_: Any) -> Any:
        process = await asyncio.create_subprocess_shell(
            command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = await process.communicate()
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
def agent_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    """The agent's and the container's environment. Under coverage, the uploaded gate measures
    itself in every process that runs it, and its lines count for its source file."""
    env = dict(os.environ)
    current = coverage.Coverage.current()
    if current is None:
        yield env
        return
    uploaded, data = tmp_path / "opt", tmp_path / "coverage"
    data.mkdir()
    rc = tmp_path / "coveragerc"
    rc.write_text(f"[run]\nsource = {uploaded}\nparallel = true\ndata_file = {data}/.coverage\n")
    env["COVERAGE_PROCESS_START"] = str(rc)
    env.pop("COVERAGE_FILE", None)  # it would beat the rc's data_file and skip the path mapping
    yield env
    copy, source = str(uploaded / "verify_gate.py"), str(verify.PAYLOAD.resolve())
    collected = current.get_data()
    for path in data.glob(".coverage.*"):
        child = coverage.CoverageData(basename=str(path))
        child.read()
        collected.update(child, map_path=lambda name: source if name == copy else name)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_env: dict[str, str]) -> Path:
    paths = verify.VerifyPaths(
        workspace=str(tmp_path / "app"),
        home=str(tmp_path / "opt"),
        state=str(tmp_path / "state"),
        system_python=sys.executable,
        reap=False,  # the agent's user is the test's own: reaping would kill the test run
    )
    monkeypatch.setattr(verify, "PATHS", paths)
    monkeypatch.setattr(
        verify,
        "tools_install_command",
        lambda p, pins: f"mkdir -p {p.home} && ln -sfn {sys.prefix} {p.tools}",
    )

    def create(runner: Any, spec: Any, logs_dir: Path, *, model_name: str | None) -> BaseAgent:
        return ScriptedAgent(logs_dir=logs_dir, model_name=model_name)

    monkeypatch.setattr("raidar.subject.workflow.runner.create_inner", create)
    ScriptedAgent.prompts = []
    ScriptedAgent.workspace = tmp_path / "app"
    ScriptedAgent.workspace.mkdir()
    return ScriptedAgent.workspace


def write(workspace: Path, **files: str) -> None:
    for name, text in files.items():
        (workspace / f"{name}.py").write_text(text)


def commit(workspace: Path, env: dict[str, str], *flags: str) -> int:
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, env=env)
    message = ["-m", "feat: add"]
    done = subprocess.run(["git", "commit", "-q", *message, *flags], cwd=workspace, env=env)
    return done.returncode
