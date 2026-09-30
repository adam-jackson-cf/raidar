"""review-loop@1 workflow agent (plan WS-C C4).

A scripted inner Harbor agent stands in for the harness and a local-shell
environment stands in for the container (mounted logs), so the loop's real
shell commands (workspace trees, git diff, log filing) run for real.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from raidar.agents.config import Harness
from raidar.components.builtins.workflow import REVIEW_LOOP
from raidar.components.evidence import StepEvidence
from raidar.components.manifest import ComponentUseContext
from raidar.components.review_loop import (
    APPROVE,
    CHANGES,
    EMPTY_FEEDBACK,
    NO_VERDICT,
    ReviewLoopAgent,
    final_message,
    parse_verdict,
    render,
)
from tests.component_support import PILOT_DIR, write_jsonl


@dataclass
class _Capabilities:
    mounted: bool = True


@dataclass
class LocalEnvironment:
    """Runs commands in a local workspace; the logs dir is 'mounted' on the host."""

    workspace: Path
    capabilities: _Capabilities = field(default_factory=_Capabilities)
    downloads: list[tuple[str, Path]] = field(default_factory=list)

    async def exec(self, command: str, **_: Any) -> ExecResult:
        done = subprocess.run(
            ["bash", "-c", command], cwd=self.workspace, capture_output=True, text=True
        )
        return ExecResult(stdout=done.stdout, stderr=done.stderr, return_code=done.returncode)

    async def download_dir(self, source_dir: str, target_dir: Path) -> None:
        self.downloads.append((source_dir, target_dir))


class ScriptedInnerAgent(BaseAgent):
    """Implements by appending to the workspace; reviews with scripted replies."""

    replies: ClassVar[list[str]] = []
    prompts: ClassVar[list[str]] = []
    fail_on: ClassVar[str | None] = None

    @staticmethod
    def name() -> str:
        return "scripted-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        cls = type(self)
        cls.prompts.append(instruction)
        reviewing = "VERDICT: APPROVE" in instruction
        if cls.fail_on == ("review" if reviewing else "work"):
            raise RuntimeError("harness crashed")
        logs = self.environment_logs_dir
        text = cls.replies.pop(0) if reviewing else "done"
        if not reviewing:
            reasoning = instruction.split("`")[1]
            await environment.exec(
                f"echo 'x = {len(cls.prompts)}' >> feature.py && echo account > {reasoning}"
            )
        line = json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}}
        )
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.prompts)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": text}],
        }
        await environment.exec(
            f"echo {shlex.quote(line)} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 100, 10, 0.01


def _agent(tmp_path: Path, rounds: int = 3) -> tuple[ReviewLoopAgent, LocalEnvironment]:
    workspace, logs = tmp_path / "app", tmp_path / "logs"
    workspace.mkdir(parents=True)
    logs.mkdir()
    (workspace / "main.py").write_text("print('hi')\n")
    (logs / "raidar-reset.json").write_text("{}")
    agent = ReviewLoopAgent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=logs,  # type: ignore[arg-type]
        inner_agent={"name": "tests.test_review_loop_workflow:ScriptedInnerAgent"},
        max_review_rounds=rounds,
        work_dir=str(tmp_path / "review"),
    )
    return agent, LocalEnvironment(workspace)


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    ScriptedInnerAgent.replies.clear()
    ScriptedInnerAgent.prompts.clear()
    ScriptedInnerAgent.fail_on = None


def _run(agent: ReviewLoopAgent, environment: LocalEnvironment) -> AgentContext:
    context = AgentContext()
    asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))
    return context


def _use() -> ComponentUseContext:
    return ComponentUseContext("00-review-loop", Harness.CODEX_CLI, PILOT_DIR)


def test_changes_then_approve_runs_fresh_sessions_and_bills_every_stage(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.replies[:] = [
        "Needs work.\nVERDICT: CHANGES\nAdd a docstring.",
        "VERDICT: APPROVE",
    ]

    context = _run(agent, environment)

    loop = context.metadata["review_loop"]  # type: ignore[index]
    assert [stage["stage"] for stage in loop["stages"]] == [
        "implement",
        "review",
        "retry",
        "review",
    ]
    assert loop["termination_reason"] == "review_approved"
    assert loop["final_verdict"] == APPROVE
    assert (context.n_input_tokens, context.n_output_tokens) == (400, 40)
    assert context.cost_usd == pytest.approx(0.04)
    assert "cost_usd" not in loop["usage_unavailable"]
    review_prompt, retry_prompt = ScriptedInnerAgent.prompts[1], ScriptedInnerAgent.prompts[2]
    assert "+x = 1" in review_prompt
    assert "Build the feature." in review_prompt
    assert "Add a docstring." in retry_prompt
    logs = tmp_path / "logs"
    trajectory = Trajectory.model_validate_json((logs / "trajectory.json").read_text())
    assert [sub.trajectory_id for sub in trajectory.subagent_trajectories or []] == [
        "stage_1_implement",
        "stage_2_review",
        "stage_3_retry",
        "stage_4_review",
    ]
    assert trajectory.final_metrics is not None
    assert trajectory.final_metrics.total_cost_usd == pytest.approx(0.04)
    assert (logs / "review-loop/stage_2_review/codex.txt").is_file()
    assert (logs / "review-loop/diffs/round_1.patch").read_text().count("feature.py") >= 1
    assert (logs / "review-loop/reasoning_round_1.md").read_text() == "account\n"
    assert (logs / "raidar-reset.json").is_file(), "step-level logs stay at the top level"
    assert not (tmp_path / "app" / "reasoning.md").exists()
    outcome = REVIEW_LOOP.probe(_use(), StepEvidence(logs), 0)
    assert outcome.passed, outcome.detail


def test_unusable_review_ends_the_loop_but_keeps_the_trajectory_valid(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.replies[:] = ["I looked at it."]

    context = _run(agent, environment)

    loop = context.metadata["review_loop"]  # type: ignore[index]
    assert loop["termination_reason"] == "review_no_verdict"
    assert loop["status"] == "failed"
    # Reviewer output format is model behaviour, not an infrastructure failure:
    # the probe must not exclude this arm's trajectory.
    outcome = REVIEW_LOOP.probe(_use(), StepEvidence(tmp_path / "logs"), 0)
    assert outcome.passed, outcome.detail
    assert "review_no_verdict" in outcome.detail


def test_rounds_run_out_with_feedback_outstanding(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path, rounds=1)
    ScriptedInnerAgent.replies[:] = ["VERDICT: CHANGES\nMore tests."]

    context = _run(agent, environment)

    loop = context.metadata["review_loop"]  # type: ignore[index]
    assert (loop["status"], loop["termination_reason"]) == ("completed", "rounds_exhausted")
    assert len(loop["stages"]) == 2


def test_a_failed_stage_is_recorded_and_the_probe_fails(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.fail_on = "review"

    context = _run(agent, environment)

    loop = context.metadata["review_loop"]  # type: ignore[index]
    assert loop["termination_reason"] == "stage_failed"
    assert "harness crashed" in loop["stages"][1]["error"]
    # The crashed reviewer reported no usage: the arm's total is unavailable,
    # never the implementer's cost alone.
    assert (context.cost_usd, context.n_input_tokens) == (None, None)
    assert loop["usage_unavailable"]["cost_usd"] == ["stage_2_review"]
    trajectory = Trajectory.model_validate_json((tmp_path / "logs/trajectory.json").read_text())
    assert trajectory.final_metrics is not None
    assert trajectory.final_metrics.total_cost_usd is None
    probe = REVIEW_LOOP.probe(_use(), StepEvidence(tmp_path / "logs"), 0)
    assert not probe.passed
    assert "no completed review stage" in probe.detail
    assert not REVIEW_LOOP.probe(_use(), StepEvidence(tmp_path / "missing"), 0).passed


def test_unmounted_environment_downloads_each_stage(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path)
    environment.capabilities.mounted = False
    ScriptedInnerAgent.replies[:] = ["VERDICT: APPROVE"]

    _run(agent, environment)

    assert [Path(source).name for source, _ in environment.downloads] == [
        "stage_1_implement",
        "stage_2_review",
    ]


def test_loop_command_failures_are_raised_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, environment = _agent(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")
    agent.options.work_dir = str(blocked / "review")  # type: ignore[union-attr]
    with pytest.raises(RuntimeError, match="review-loop command failed"):
        _run(agent, environment)

    agent, environment = _agent(tmp_path / "second")

    async def broken_capture(*_: Any) -> str:
        raise RuntimeError("diff exploded")

    monkeypatch.setattr(agent, "_capture", broken_capture)
    with pytest.raises(RuntimeError, match="diff exploded"):
        _run(agent, environment)
    record = json.loads((tmp_path / "second/logs/review-loop/review-loop.json").read_text())
    assert record["error"] == "RuntimeError: diff exploded"


def test_failed_implement_stage_skips_the_review(tmp_path: Path) -> None:
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.fail_on = "work"

    loop = _run(agent, environment).metadata["review_loop"]  # type: ignore[index]

    assert [stage["stage"] for stage in loop["stages"]] == ["implement"]
    assert loop["termination_reason"] == "stage_failed"


def test_large_diffs_are_truncated_for_the_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("raidar.components.review_loop.MAX_DIFF_CHARS", 40)
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.replies[:] = ["VERDICT: APPROVE"]
    asyncio.run(agent.setup(cast(BaseEnvironment, environment)))

    _run(agent, environment)

    assert "diff truncated at 40 characters" in ScriptedInnerAgent.prompts[1]
    assert (agent.name(), agent.version()) == ("raidar-review-loop", "1")


def test_verdict_parsing_and_final_message_follow_factory_x_loops() -> None:
    assert parse_verdict("quote: VERDICT: CHANGES x\nVERDICT: APPROVE") == (APPROVE, "")
    assert parse_verdict("VERDICT: APPROVE\nVERDICT: CHANGES fix x") == (CHANGES, "fix x")
    assert parse_verdict("VERDICT: CHANGES   ") == (EMPTY_FEEDBACK, "")
    assert parse_verdict("nothing") == (NO_VERDICT, "")
    assert render("a %%%X%%% {b}", {"X": "{y}"}) == "a {y} {b}"


def test_final_message_reads_claude_result_stream(tmp_path: Path) -> None:
    write_jsonl(
        tmp_path / "claude-code.txt",
        [{"type": "assistant"}, {"type": "result", "result": "VERDICT: APPROVE"}],
    )

    assert final_message(tmp_path) == "VERDICT: APPROVE"
