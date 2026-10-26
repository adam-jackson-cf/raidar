"""The verify stage in one primary session: the hook, the seal, fix turns, cuts and ``G``.

The workflow runner drives a scripted native agent that edits and commits in a real git
workspace; the stage's container paths point into a temporary directory and its commands run
on the host's shell, as root would run them in the container. The gate's tools are this test
environment's own ruff and pytest (a real install of the pinned tools is what the smoke proves).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, ClassVar

import coverage
import pytest
from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext

from raidar.engine.health.gate import gate_family
from raidar.subject.workflow import verify
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import PROMPT_FILE, RECORD_FILE, WORKFLOW_DIR, WorkflowRunner
from raidar.subject.workflow.verify_gate import RULES_BEGIN
from tests.release_support import REAL_LAB

TASK = "Add `add(a, b)` to calc.py, with a test."
CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
PASSING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n"
FAILING = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(2, 3) == 6\n"

AgentTurn = Callable[[Path, str], None]


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


class ScriptedAgent(BaseAgent):
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
        (self.logs_dir / "trajectory.json").write_text(json.dumps({"session_id": "session-1"}))
        turns = len(cls.prompts)  # the session's cumulative totals, as codex reports them
        context.n_input_tokens, context.n_cache_tokens = 1000 * turns, 600 * turns
        context.n_output_tokens, context.cost_usd = 50 * turns, None
        cls.turns.pop(0)(cls.workspace, instruction)


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
        workspace=str(tmp_path / "app"), home=str(tmp_path / "opt"), state=str(tmp_path / "state")
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


def definition(**lint: str) -> dict[str, Any]:
    """one-shot-verify@1 with a lint and a tests check (and the lint check's tool replaced)."""
    resolved = load_workflow(REAL_LAB / "workflows", "one-shot-verify").to_json()
    stages: Any = resolved["stages"]
    lint_check = {"name": "lint", "category": "lint", "tool": "ruff==0.8.6", "run": "ruff check"}
    tests_check = {"name": "tests", "category": "tests", "tool": "pytest==9.1.1"}
    stages["verify"]["checks"] = [
        {**lint_check, **lint, "blocking": True},
        {**tests_check, "blocking": True},
    ]
    return resolved


def run(tmp_path: Path, env: dict[str, str], turns: list[AgentTurn], **lint: str) -> dict[str, Any]:
    """Run one checkpoint and return its workflow record; raises what the run raised."""
    ScriptedAgent.turns = list(turns)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    runner = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="one-shot-verify@1",
        definition=definition(**lint),
    )
    asyncio.run(runner.run(TASK, LocalEnvironment(env), AgentContext()))  # type: ignore[arg-type]
    return recorded(tmp_path)


def recorded(tmp_path: Path) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (tmp_path / "logs" / WORKFLOW_DIR / RECORD_FILE).read_text()
    )
    return record


def health(tmp_path: Path) -> dict[str, str]:
    return {c.id: c.status for c in gate_family([("checkpoint_1", tmp_path / "logs")])}


def hook_verdicts(tmp_path: Path, record: dict[str, Any]) -> list[str]:
    lines = (tmp_path / "logs" / record["verify"]["hook_log"]).read_text().splitlines()
    return [json.loads(line)["verdict"] for line in lines]


def test_a_commit_the_hook_passes_is_sealed_and_the_checkpoint_ends(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    seen: dict[str, Any] = {}

    def build(ws: Path, prompt: str) -> None:
        seen["rules"] = (ws / "AGENTS.md").read_text()
        write(ws, calc=CODE, test_calc=PASSING)
        seen["commit"] = commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build])

    stage = record["verify"]
    assert (stage["ending"], stage["fix_rounds"], seen["commit"]) == ("gate_passed", 0, 0)
    assert [turn["stage"] for turn in record["turns"]] == ["build"]
    (gate,) = stage["gates"]
    assert (gate["who"], gate["verdict"], gate["commit"]) == ("seal", "passed", None)
    assert [c["status"] for c in gate["checks"]] == ["passed", "passed"]
    assert hook_verdicts(tmp_path, record) == ["passed"]
    prompt = (tmp_path / "logs" / WORKFLOW_DIR / "build-1" / PROMPT_FILE).read_text()
    assert "Your work is read from the repository's commits." in prompt
    assert prompt.endswith(TASK)
    assert "%%%" not in prompt
    assert seen["rules"].lstrip("\n").startswith(RULES_BEGIN)
    assert not (workspace / "AGENTS.md").exists()  # the stage's own file is gone before grading
    assert record["instructions"] == ["workflow/verify/rules.md"]
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}


def test_a_refused_tree_resumes_the_session_with_the_gate_output_until_it_passes(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    commits: list[int] = []

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)
        commits.append(commit(ws, agent_env))

    def fix(ws: Path, prompt: str) -> None:
        write(ws, test_calc=PASSING)
        commits.append(commit(ws, agent_env))

    record = run(tmp_path, agent_env, [build, fix])

    stage = record["verify"]
    assert commits == [1, 0]  # the hook refused the first commit inside the session
    assert (stage["ending"], stage["fix_rounds"]) == ("gate_passed", 1)
    assert [g["verdict"] for g in stage["gates"]] == ["refused", "passed"]
    resumed, fix_prompt = ScriptedAgent.prompts[1]
    assert resumed
    assert "refused your commit" in fix_prompt
    assert "test_add" in fix_prompt
    turns = record["turns"]
    assert [(t["stage"], t["resumed"]) for t in turns] == [("build", False), ("verify", True)]
    assert turns[1]["tokens"] == {"input": 1000, "cached": 600, "output": 50, "cost_usd": None}
    assert hook_verdicts(tmp_path, record) == ["refused", "passed"]
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}


def test_a_gate_that_cannot_run_ends_the_checkpoint_without_a_fix_turn(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        commit(ws, agent_env)

    record = run(tmp_path, agent_env, [build], tool="black==24.10.0", run="black --check")

    stage = record["verify"]
    assert (stage["ending"], stage["fix_rounds"], len(record["turns"])) == ("gate_error", 0, 1)
    assert stage["gates"][0]["verdict"] == "error"
    assert "tool not installed" in stage["gates"][0]["error"]
    assert stage["restored"]["gate"] is None  # no seal decided a tree to go back to
    assert hook_verdicts(tmp_path, record) == ["error"]


def test_a_deadline_in_a_fix_turn_restores_the_last_gated_tree(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=FAILING)

    def cut(ws: Path, prompt: str) -> None:
        write(ws, calc="def add(a, b):\n    return\n", scratch="x = 1\n")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        run(tmp_path, agent_env, [build, cut])

    stage = recorded(tmp_path)["verify"]
    assert stage["ending"] == "deadline"
    assert (stage["restored"]["gate"], stage["restored"]["outcome"]["restored"]) == (1, True)
    assert (workspace / "calc.py").read_text() == CODE
    assert (workspace / "test_calc.py").read_text() == FAILING
    assert not (workspace / "scratch.py").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_a_commit_made_around_the_hook_is_found_as_a_bypass(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        commit(ws, agent_env, "--no-verify")

    record = run(tmp_path, agent_env, [build])

    assert record["verify"]["ending"] == "gate_passed"  # the seal itself passes the tree
    assert hook_verdicts(tmp_path, record) == []
    checks = {c.id: c for c in gate_family([("checkpoint_1", tmp_path / "logs")])}
    assert checks["G2"].status == "violation"
    assert "no hook run passed" in checks["G2"].detail
