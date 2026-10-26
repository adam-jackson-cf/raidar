"""The workflow runner: one build turn per checkpoint, its logs, record and context totals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext

from raidar.contract.paths import workflows_root
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.runner import (
    PROMPT_FILE,
    RECORD_FILE,
    TRAJECTORY_FILE,
    WORKFLOW_DIR,
    WorkflowRunner,
)
from tests.experiment_spec_support import REPO_ROOT

INSTRUCTION = "Add a function `add(a, b)` to main.py."
ONE_SHOT_PROMPT = f"Work the issue below per the repository's own rules.\n\n{INSTRUCTION}"
SESSION = "session-1"


class FakeInner(BaseAgent):
    """A native agent that records its prompt and leaves a trajectory and token usage behind."""

    prompts: list[str] = []
    tokens: tuple[int, int, int, float] | None = (120, 20, 30, 0.02)
    failure: Exception | None = None

    @staticmethod
    def name() -> str:
        return "fake-inner"

    def version(self) -> str | None:
        return None

    async def setup(self, environment: Any) -> None:
        return None

    async def run(self, instruction: str, environment: Any, context: AgentContext) -> None:
        type(self).prompts.append(instruction)
        (self.logs_dir / TRAJECTORY_FILE).write_text(
            json.dumps({"session_id": SESSION, "steps": [{"message": "done"}]})
        )
        if type(self).failure is not None:
            raise type(self).failure
        if type(self).tokens is not None:
            n_input, n_cached, n_output, cost = type(self).tokens
            context.n_input_tokens = n_input
            context.n_cache_tokens = n_cached
            context.n_output_tokens = n_output
            context.cost_usd = cost


class FakeEnvironment:
    """A mounted-logs environment: the in-container copy of the turn's logs is a no-op here."""

    capabilities = SimpleNamespace(mounted=True)

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(self, command: str, **_: Any) -> Any:
        self.commands.append(command)
        return SimpleNamespace(return_code=0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def inner(monkeypatch: pytest.MonkeyPatch) -> type[FakeInner]:
    FakeInner.prompts = []
    FakeInner.tokens = (120, 20, 30, 0.02)
    FakeInner.failure = None

    def create(runner: Any, spec: Any, logs_dir: Path, *, model_name: str | None) -> FakeInner:
        return FakeInner(logs_dir=logs_dir, model_name=model_name)

    monkeypatch.setattr("raidar.subject.workflow.runner.create_inner", create)
    return FakeInner


def _runner(logs_dir: Path) -> WorkflowRunner:
    definition = load_workflow(workflows_root(REPO_ROOT), "one-shot")
    return WorkflowRunner(
        logs_dir=logs_dir,
        model_name="openai/gpt-6-luna",
        inner_agent={"name": "codex", "kwargs": {"version": "0.156.1"}},
        workflow="one-shot@1",
        definition=definition.to_json(),
    )


def _run(logs_dir: Path) -> tuple[AgentContext, FakeEnvironment]:
    context = AgentContext()
    environment = FakeEnvironment()
    asyncio.run(_runner(logs_dir).run(INSTRUCTION, environment, context))  # type: ignore[arg-type]
    return context, environment


def test_a_one_shot_checkpoint_is_one_build_turn_sent_the_workflow_prompt(tmp_path: Path) -> None:
    context, environment = _run(tmp_path)

    turn_dir = tmp_path / WORKFLOW_DIR / "build-1"
    assert (turn_dir / PROMPT_FILE).read_text() == ONE_SHOT_PROMPT
    assert FakeInner.prompts == [ONE_SHOT_PROMPT]
    assert len(environment.commands) == 1  # the turn's logs were copied once

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["kind"] == "raidar-workflow"
    assert record["workflow"] == "one-shot@1"
    (turn,) = record["turns"]
    assert (turn["stage"], turn["turn"], turn["log_dir"]) == ("build", 1, "workflow/build-1")
    assert (turn["session_id"], turn["resumed"], turn["error"]) == (SESSION, False, None)
    expected = {"input": 120, "cached": 20, "output": 30, "cost_usd": 0.02}
    assert turn["tokens"] == turn["session_total"] == record["tokens"] == expected

    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (
        120,
        20,
        30,
    )
    assert context.cost_usd == 0.02
    assert context.metadata is not None
    assert context.metadata["workflow"] == record
    assert (tmp_path / TRAJECTORY_FILE).read_bytes() == (turn_dir / TRAJECTORY_FILE).read_bytes()


def test_context_totals_are_set_only_when_every_turns_usage_is_known(tmp_path: Path) -> None:
    FakeInner.tokens = None

    context, _ = _run(tmp_path)

    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
        context.cost_usd,
    ) == (None, None, None, None)
    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["tokens"] is None
    assert record["turns"][0]["tokens"] is None
    assert (tmp_path / WORKFLOW_DIR / "build-1" / PROMPT_FILE).read_text() == ONE_SHOT_PROMPT


def test_a_failing_turn_is_recorded_and_the_error_still_reaches_harbor(tmp_path: Path) -> None:
    FakeInner.failure = RuntimeError("agent crashed")
    context = AgentContext()

    with pytest.raises(RuntimeError, match="agent crashed"):
        asyncio.run(
            _runner(tmp_path).run(INSTRUCTION, FakeEnvironment(), context)  # type: ignore[arg-type]
        )

    record = json.loads((tmp_path / WORKFLOW_DIR / RECORD_FILE).read_text())
    assert record["turns"][0]["error"] == "RuntimeError: agent crashed"
    assert (tmp_path / TRAJECTORY_FILE).is_file()
    assert context.n_input_tokens is None
