"""The verify stage in one primary session: the hook, the seal, fix turns, cuts and ``G``.

The workflow runner drives a scripted native agent that edits and commits in a real git
workspace; the stage's container paths point into a temporary directory and its commands run
on the host's shell, as root would run them in the container. The gate's tools are this test
environment's own ruff and pytest (a real install of the pinned tools is what the smoke proves).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from harbor.models.agent.context import AgentContext

from raidar.engine.health.gate import gate_family
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import PROMPT_FILE, RECORD_FILE, WORKFLOW_DIR, WorkflowRunner
from raidar.subject.workflow.verify_gate import RULES_BEGIN
from tests.release_support import REAL_LAB
from tests.workflow_support import (
    CODE,
    FAILING,
    PASSING,
    AgentTurn,
    LocalEnvironment,
    ScriptedAgent,
    commit,
    write,
)

TASK = "Add `add(a, b)` to calc.py, with a test."


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


def test_ignored_files_are_checked_without_false_hook_seal_disagreement(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    commits: list[int] = []

    def build(ws: Path, prompt: str) -> None:
        write(ws, calc=CODE, test_calc=PASSING)
        (ws / ".gitignore").write_text("ignored/\n")
        ignored = ws / "ignored"
        ignored.mkdir()
        bad = ignored / "bad.py"
        bad.write_text("import os\n")
        commits.append(commit(ws, agent_env))
        bad.unlink()
        commits.append(commit(ws, agent_env))

    record = run(tmp_path, agent_env, [build])
    assert commits == [1, 0]
    assert record["verify"]["ending"] == "gate_passed"
    assert health(tmp_path) == {"G1": "pass", "G2": "pass", "G3": "pass"}
    lines = (tmp_path / "logs" / record["verify"]["hook_log"]).read_text().splitlines()
    first, second = map(json.loads, lines)
    assert first["tree"] == second["tree"]


def test_a_changed_ignored_input_reruns_a_refused_tree(
    tmp_path: Path, workspace: Path, agent_env: dict[str, str]
) -> None:
    def build(ws: Path, prompt: str) -> None:
        write(
            ws,
            calc=CODE,
            test_calc="from pathlib import Path\n\n\ndef test_settings() -> None:\n"
            '    assert Path("settings.txt").read_text() == "fixed"\n',
        )
        (ws / ".gitignore").write_text("settings.txt\n")
        (ws / "settings.txt").write_text("broken")

    def fix(ws: Path, prompt: str) -> None:
        (ws / "settings.txt").write_text("fixed")

    record = run(tmp_path, agent_env, [build, fix])
    first, second = record["verify"]["gates"]
    assert first["tree"] == second["tree"]
    assert [first["verdict"], second["verdict"]] == ["refused", "passed"]
    assert second["reused_from"] is None
    assert record["verify"]["ending"] == "gate_passed"


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
