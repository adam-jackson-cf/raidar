"""review-loop@1 and review-loop@2 workflow agents (plan WS-C C4; gate packet F3).

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
from raidar.components.catalog.workflow import REVIEW_LOOP
from raidar.components.catalog.workflow_v2 import REVIEW_LOOP_V2
from raidar.components.definition.evidence import StepEvidence
from raidar.components.definition.manifest import ComponentUseContext
from raidar.components.definition.registry import fingerprint
from raidar.components.runtime.review_loop import (
    APPROVE,
    CHANGES,
    EMPTY_FEEDBACK,
    NO_VERDICT,
    PROMPT_ARG_BUDGET,
    STAGE_PROMPTS,
    ReviewLoopAgent,
    final_message,
    fit_diff,
    parse_verdict,
    render,
)
from raidar.components.runtime.review_loop_v2 import ReviewLoopV2Agent, ReviewParse, parse_review
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
        reviewed = any("VERDICT: APPROVE" in prompt for prompt in cls.prompts[:-1])
        logs = self.environment_logs_dir
        if not reviewing and reviewed and cls.fail_on == "work-after-review":
            raise RuntimeError("harness crashed")
        if not reviewing and reviewed and cls.fail_on == "hang-after-review":
            line = json.dumps({"type": "turn.started"})
            await environment.exec(f"echo {shlex.quote(line)} > {logs}/codex.txt")
            await asyncio.sleep(60)
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
    monkeypatch.setattr("raidar.components.runtime.review_loop.MAX_DIFF_CHARS", 40)
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.replies[:] = ["VERDICT: APPROVE"]
    asyncio.run(agent.setup(cast(BaseEnvironment, environment)))

    _run(agent, environment)

    assert "diff truncated at 40 characters" in ScriptedInnerAgent.prompts[1]
    assert (agent.name(), agent.version()) == ("raidar-review-loop", "1")


def test_the_reviewer_diff_leaves_out_what_the_snapshot_leaves_out(tmp_path: Path) -> None:
    """A ``.venv`` from ``uv run`` filled the pilot reviewer's diff (6.9 MB) and its prompt."""
    agent, environment = _agent(tmp_path)
    ScriptedInnerAgent.replies[:] = ["VERDICT: APPROVE"]
    asyncio.run(agent.setup(cast(BaseEnvironment, environment)))
    for junk in (".venv/lib/site.py", ".pytest_cache/v/x", "pkg/__pycache__/m.pyc"):
        (tmp_path / "app" / junk).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / junk).write_text("junk\n")

    _run(agent, environment)

    review_prompt = ScriptedInnerAgent.prompts[1]
    assert "feature.py" in review_prompt
    assert ".venv" not in review_prompt
    assert ".pytest_cache" not in review_prompt


def _quoted(text: str) -> int:
    return len(shlex.quote(text).encode())


def test_the_quoted_review_prompt_fits_one_shell_argument() -> None:
    """Linux caps one argv string at 128 KiB; a 200k-char diff crashed the reviewer (exit 255)."""
    tokens = {"TASK": "spec ' é" * 2000, "REASONING_PATH": "/tmp/r.md"}
    template = STAGE_PROMPTS["review"]
    diff = ("+ it's a line with 'quotes' and é\n") * 20_000
    fitted = fit_diff(template, tokens, diff)
    assert fitted.endswith("... diff truncated to fit the harness prompt limit ...\n")
    assert _quoted(render(template, {**tokens, "DIFF": fitted})) <= PROMPT_ARG_BUDGET
    assert PROMPT_ARG_BUDGET < 128 * 1024
    small = "+ x = 1\n"
    assert fit_diff(template, tokens, small) == small


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


# --- review-loop@2 (pilot v2, gate packet F3) -------------------------------------

PILOT_REPLIES = Path(__file__).parent / "fixtures" / "review_loop" / "pilot-reviewer-replies.json"


def _pilot_reviews() -> list[dict[str, str]]:
    return list(json.loads(PILOT_REPLIES.read_text(encoding="utf-8"))["reviews"])


def _agent_v2(
    tmp_path: Path, rounds: int = 2, template: str | None = None
) -> tuple[ReviewLoopV2Agent, LocalEnvironment]:
    workspace, logs = tmp_path / "app", tmp_path / "logs"
    workspace.mkdir(parents=True)
    logs.mkdir()
    (logs / "raidar-reset.json").write_text("{}")
    kwargs: dict[str, Any] = {}
    if template is not None:
        path = tmp_path / "framing.md"
        path.write_text(template)
        kwargs["prompt_template_path"] = str(path)
    agent = ReviewLoopV2Agent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=logs,  # type: ignore[arg-type]
        inner_agent={
            "name": "tests.test_review_loop_workflow:ScriptedInnerAgent",
            "kwargs": kwargs,
        },
        max_review_rounds=rounds,
        component="review-loop@2",
        work_dir=str(tmp_path / "review"),
    )
    return agent, LocalEnvironment(workspace)


def test_v2_parses_every_pilot_review_that_v1_could_not_act_on() -> None:
    """The 18 forge (and 2 one-shot) EMPTY_FEEDBACK replies carry their feedback before the line."""
    reviews = _pilot_reviews()
    unusable = [review for review in reviews if review["v1_verdict"] == EMPTY_FEEDBACK]
    assert len([r for r in unusable if r["run"] == "forge-04"]) == 18
    for review in unusable:
        assert parse_verdict(review["reply"]) == (EMPTY_FEEDBACK, ""), "review-loop@1 is unchanged"
        parsed = parse_review(review["reply"])
        assert (parsed.verdict, parsed.feedback_position) == (CHANGES, "before"), review["step"]
        assert parsed.feedback == review["reply"][: review["reply"].rfind("VERDICT")].strip()
    # What v1 read as a ruling, v2 reads the same way.
    for review in reviews:
        v1, v2 = review["v1_verdict"], parse_review(review["reply"]).verdict
        if v1 in (APPROVE, CHANGES):
            assert v2 == v1, (review["trial"], review["step"], review["stage"])
    # The one reply without any marker stays unusable: the loop re-asks it.
    (no_verdict,) = [review for review in reviews if review["v1_verdict"] == NO_VERDICT]
    assert parse_review(no_verdict["reply"]).verdict == NO_VERDICT


def test_v2_verdict_markers_tolerate_markdown_and_inline_feedback() -> None:
    assert parse_review("Looks fine.\n**VERDICT: APPROVE**") == ReviewParse(APPROVE, "", "none", 1)
    assert parse_review("VERDICT: CHANGES — fix z").feedback == "fix z"
    both = parse_review("Gap: x.\n`VERDICT: CHANGES`\n- also y")
    assert (both.verdict, both.feedback, both.feedback_position) == (
        CHANGES,
        "Gap: x.\n\n- also y",
        "both",
    )
    assert parse_review("VERDICT: CHANGES\n").verdict == EMPTY_FEEDBACK
    assert parse_review("VERDICT: CHANGES x\nVERDICT: APPROVE").verdict == APPROVE
    assert parse_review("I would not approve this.").verdict == NO_VERDICT


def test_v2_feedback_before_the_verdict_drives_the_fix_round(tmp_path: Path) -> None:
    agent, environment = _agent_v2(tmp_path)
    ScriptedInnerAgent.replies[:] = [
        "Handle the empty input.\n\nVERDICT: CHANGES",
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
    assert (loop["workflow"], loop["termination_reason"]) == ("review-loop@2", "review_approved")
    assert "Handle the empty input." in ScriptedInnerAgent.prompts[2]
    assert [
        (p["round"], p["attempt"], p["verdict"], p["feedback_position"]) for p in loop["parses"]
    ] == [
        (1, 1, CHANGES, "before"),
        (2, 1, APPROVE, "none"),
    ]
    record = json.loads((tmp_path / "logs/review-loop/review-loop.json").read_text())
    assert record["parses"] == loop["parses"]
    trajectory = Trajectory.model_validate_json((tmp_path / "logs/trajectory.json").read_text())
    assert (trajectory.agent.version, agent.version()) == ("2", "2")
    assert REVIEW_LOOP_V2.probe(_use(), StepEvidence(tmp_path / "logs"), 0).passed


def test_v2_reasks_an_unusable_review_once(tmp_path: Path) -> None:
    agent, environment = _agent_v2(tmp_path, rounds=1)
    ScriptedInnerAgent.replies[:] = ["These should be fixed before approval.", "VERDICT: APPROVE"]

    loop = _run(agent, environment).metadata["review_loop"]  # type: ignore[index]

    assert [stage["stage"] for stage in loop["stages"]] == ["implement", "review", "review"]
    assert loop["termination_reason"] == "review_approved"
    assert [(p["attempt"], p["verdict"]) for p in loop["parses"]] == [
        (1, NO_VERDICT),
        (2, APPROVE),
    ]
    reask = ScriptedInnerAgent.prompts[2]
    assert "These should be fixed before approval." in reask
    assert "+x = 1" in reask, "the re-asked reviewer sees the same diff"


def test_v2_falls_back_to_the_work_as_it_stands_after_a_second_unusable_review(
    tmp_path: Path,
) -> None:
    agent, environment = _agent_v2(tmp_path)
    ScriptedInnerAgent.replies[:] = ["Not sure.", "VERDICT: CHANGES"]

    loop = _run(agent, environment).metadata["review_loop"]  # type: ignore[index]

    assert [stage["stage"] for stage in loop["stages"]] == ["implement", "review", "review"]
    assert (loop["status"], loop["termination_reason"]) == (
        "completed",
        "review_unusable_after_reask",
    )
    assert [p["verdict"] for p in loop["parses"]] == [NO_VERDICT, EMPTY_FEEDBACK]
    assert loop["final_verdict"] == EMPTY_FEEDBACK
    assert REVIEW_LOOP_V2.probe(_use(), StepEvidence(tmp_path / "logs"), 0).passed


def test_v2_frames_the_implementer_task_but_not_the_reviewer(tmp_path: Path) -> None:
    """prompt-wrapper@1 under review-loop@2: the reviewer must not be told to build."""
    framing = "You are working non-interactively in /app.\n\n{{ instruction }}\n"
    agent, environment = _agent_v2(tmp_path, template=framing)
    ScriptedInnerAgent.replies[:] = ["Gap.\nVERDICT: CHANGES", "VERDICT: APPROVE"]

    _run(agent, environment)

    implement, review, retry, _ = ScriptedInnerAgent.prompts
    line = "You are working non-interactively in /app."
    assert line in implement
    assert line in retry
    assert line not in review
    assert "Build the feature." in review


def test_v2_component_wraps_with_its_own_agent_and_leaves_v1_alone() -> None:
    use = ComponentUseContext(
        "05-review-loop", Harness.CODEX_CLI, PILOT_DIR, with_={"max_review_rounds": 2}
    )
    v1, v2 = REVIEW_LOOP.contribute(use), REVIEW_LOOP_V2.contribute(use)

    assert v2.agent_wrapper is not None
    assert v1.agent_wrapper is not None
    assert (
        v2.agent_wrapper.import_path == "raidar.components.runtime.review_loop_v2:ReviewLoopV2Agent"
    )
    assert dict(v2.agent_wrapper.kwargs) == {"max_review_rounds": 2, "component": "review-loop@2"}
    assert v1.agent_wrapper.import_path == "raidar.components.runtime.review_loop:ReviewLoopAgent"
    assert fingerprint(REVIEW_LOOP_V2) != fingerprint(REVIEW_LOOP)
    assert "raidar/components/runtime/review_loop_v2.py" not in REVIEW_LOOP.manifest.fingerprint


def test_v2_reraises_a_failed_stage_so_both_arms_classify_it_alike(tmp_path: Path) -> None:
    agent, environment = _agent_v2(tmp_path)
    ScriptedInnerAgent.replies[:] = ["Fix it.\n\nVERDICT: CHANGES"]
    ScriptedInnerAgent.fail_on = "work-after-review"
    context = AgentContext()

    with pytest.raises(RuntimeError, match="harness crashed"):
        asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))

    loop = context.metadata["review_loop"]  # type: ignore[index]
    assert loop["termination_reason"] == "stage_failed"
    record = json.loads((tmp_path / "logs/review-loop/review-loop.json").read_text())
    assert record["stages"][-1]["stage"] == "retry"
    probe = REVIEW_LOOP_V2.probe(_use(), StepEvidence(tmp_path / "logs"), 0)
    assert probe.passed, probe.detail
    assert "classified by its exception" in probe.detail


def test_v2_files_a_cancelled_stage_before_the_timeout_propagates(tmp_path: Path) -> None:
    agent, environment = _agent_v2(tmp_path)
    ScriptedInnerAgent.replies[:] = ["Fix it.\n\nVERDICT: CHANGES"]
    ScriptedInnerAgent.fail_on = "hang-after-review"
    context = AgentContext()

    async def timed_out() -> None:
        await asyncio.wait_for(
            agent.run("Build the feature.", cast(BaseEnvironment, environment), context), 2
        )

    with pytest.raises(TimeoutError):
        asyncio.run(timed_out())

    loop = context.metadata["review_loop"]  # type: ignore[index]
    retry = loop["stages"][-1]
    assert retry["stage"] == "retry"
    assert retry["error"].startswith("CancelledError")
    assert (tmp_path / "logs/review-loop" / "stage_3_retry" / "codex.txt").is_file()
    assert not (tmp_path / "logs/codex.txt").exists()
    assert REVIEW_LOOP_V2.probe(_use(), StepEvidence(tmp_path / "logs"), 0).passed


def test_v2_a_quoted_marker_after_the_ruling_line_cannot_overturn_it() -> None:
    parsed = parse_review("VERDICT: CHANGES\n- fix x\n\n(After that it would be VERDICT: APPROVE)")
    assert parsed.verdict == CHANGES
    assert "fix x" in parsed.feedback
    inline = parse_review("Handle empty input first ... **VERDICT: CHANGES**")
    assert inline.verdict == CHANGES
    assert parse_review("Looks good.\n\n**VERDICT: APPROVE**").verdict == APPROVE
