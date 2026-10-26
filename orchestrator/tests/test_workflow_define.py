"""The real define command in a scripted builder's one primary session."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.engine.health.definition import definition_family
from raidar.subject.workflow import define
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import WorkflowRunner
from tests.release_support import REAL_LAB
from tests.test_workflow_verify import (
    CODE,
    PASSING,
    AgentTurn,
    LocalEnvironment,
    ScriptedAgent,
    commit,
    write,
)

pytest_plugins = ("tests.test_workflow_verify",)

TASK = "Add an add function that returns the sum of its two numbers."
BLOCK = """title: Addition
objective: Add two numbers and return their sum.
feature F1: Addition
outcome: Callers can add two numbers.
ac F1.AC1: Addition returns the sum of two numbers.
source: U1
aspect: two numbers are supplied -> their sum is returned
example: `2` and `3` -> `5`
"""
ASK = """question F1: Which arithmetic operation is needed?
kind: missing_input
proposal: Add the numbers
option: Multiply the numbers
"""


@pytest.fixture
def define_paths(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> define.DefinePaths:
    paths = define.DefinePaths(
        workspace=str(workspace),
        home=str(tmp_path / "define-opt"),
        command=str(tmp_path / "define"),
    )
    monkeypatch.setattr(define, "PATHS", paths)
    monkeypatch.setattr(
        define,
        "tools_install_command",
        lambda p: f"mkdir -p {shlex.quote(p.home)} && ln -sfn {shlex.quote(sys.prefix)} "
        f"{shlex.quote(p.home + '/tools')}",
    )
    return paths


def call(paths: define.DefinePaths, env: dict[str, str], *args: str, block: str = "") -> int:
    done = subprocess.run(
        [paths.command, *args],
        input=block,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert done.returncode != 126, done.stderr + done.stdout
    return done.returncode


def run(
    tmp_path: Path,
    env: dict[str, str],
    turns: list[AgentTurn],
    *,
    verify: bool = True,
    max_refinement_rounds: int = 2,
) -> dict[str, Any]:
    ScriptedAgent.turns = list(turns)
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    definition: Any = load_workflow(REAL_LAB / "workflows", "define-build-verify").to_json()
    definition.pop("components")
    for stage in definition["stages"].values():
        stage.pop("skills", None)
    definition["stages"]["define"]["max_refinement_rounds"] = max_refinement_rounds
    if not verify:
        definition["stages"].pop("verify")
        definition["stages"]["build"]["prompt"] = definition["stages"]["build"]["prompt"].replace(
            "%%%VERIFY%%%", ""
        )
    runner_instance = WorkflowRunner(
        logs_dir=logs,
        environment_logs_dir=PurePosixPath(logs),
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex"},
        workflow="define-build-verify@1",
        definition=definition,
    )
    asyncio.run(runner_instance.run(TASK, LocalEnvironment(env), AgentContext()))  # type: ignore[arg-type]
    return json.loads((logs / "workflow" / "workflow.json").read_text())


def e_status(tmp_path: Path) -> dict[str, str]:
    return {
        check.id: check.status for check in definition_family([("checkpoint_1", tmp_path / "logs")])
    }


def test_a_sealed_definition_is_available_before_build_and_recorded_after_stop(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        assert call(define_paths, agent_env, "put", block=BLOCK) == 0
        assert call(define_paths, agent_env, "done", "ready") == 0
        assert "Addition returns" in (ws / ".raidar/feature-definition.md").read_text()
        write(ws, calc=CODE, test_calc=PASSING)
        assert commit(ws, agent_env) == 0

    record = run(tmp_path, agent_env, [build])
    assert record["define"]["ending"] == "definition_ready"
    assert record["verify"]["ending"] == "gate_passed"
    assert record["define"]["repairs"] == []
    assert e_status(tmp_path) == {"E1": "pass", "E2": "pass", "E3": "pass"}
    tracked = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=define_paths.workspace, text=True
    )
    assert ".raidar" not in tracked


def test_unsealed_gets_one_repair_then_unusable_and_the_build_stands(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        assert call(define_paths, agent_env, "put", block=BLOCK) == 0
        write(ws, calc=CODE)

    def refuse_to_seal(ws: Path, prompt: str) -> None:
        assert "define done" in prompt
        assert call(define_paths, agent_env, "check") == 0

    record = run(tmp_path, agent_env, [build, refuse_to_seal], verify=False)
    assert record["define"]["ending"] == "definition_unusable"
    assert len(record["define"]["repairs"]) == 1
    assert [(t["stage"], t["resumed"]) for t in record["turns"]] == [
        ("build", False),
        ("define", True),
    ]
    assert (Path(define_paths.workspace) / "calc.py").read_text() == CODE
    assert record["define"]["definition"] is None


def test_ask_accepts_each_proposal_as_an_answer_unit_and_resumes_to_a_seal(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str]
) -> None:
    def ask(ws: Path, prompt: str) -> None:
        assert call(define_paths, agent_env, "put", block=ASK) == 0
        assert call(define_paths, agent_env, "done", "ask") == 0

    def build(ws: Path, prompt: str) -> None:
        assert "A1:" in prompt
        assert "accepted: Add the numbers" in prompt
        assert call(define_paths, agent_env, "put", block=BLOCK) == 0
        assert call(define_paths, agent_env, "done", "ready") == 0
        write(ws, calc=CODE, test_calc=PASSING)

    record = run(tmp_path, agent_env, [ask, build])
    assert record["define"]["ending"] == "definition_ready"
    assert record["define"]["questions"][0]["answers"][0]["outcome"] == "accepted"
    assert record["define"]["repairs"] == []
    assert e_status(tmp_path) == {"E1": "pass", "E2": "pass", "E3": "pass"}


def test_the_hook_refuses_every_commit_until_the_definition_is_sealed(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        assert commit(ws, agent_env) == 1
        assert call(define_paths, agent_env, "put", block=BLOCK) == 0
        assert commit(ws, agent_env) == 1
        assert call(define_paths, agent_env, "done", "ready") == 0
        assert commit(ws, agent_env) == 0

    record = run(tmp_path, agent_env, [build])
    hook = tmp_path / "logs" / record["verify"]["hook_log"]
    decisions = [json.loads(line) for line in hook.read_text().splitlines()]
    assert [d["verdict"] for d in decisions] == ["refused", "refused", "passed"]
    assert [d["checks"][0]["name"] for d in decisions] == ["define.unsealed"] * 3


def test_a_question_round_limit_is_unusable_not_a_silent_pass(
    tmp_path: Path, define_paths: define.DefinePaths, agent_env: dict[str, str]
) -> None:
    def ask(ws: Path, prompt: str) -> None:
        assert call(define_paths, agent_env, "put", block=ASK) == 0
        assert call(define_paths, agent_env, "done", "ask") == 0

    record = run(tmp_path, agent_env, [ask], verify=False, max_refinement_rounds=0)
    assert record["define"]["ending"] == "definition_unusable"
    assert record["define"]["questions"][0]["answers"] == []
    assert record["define"]["repairs"] == []
