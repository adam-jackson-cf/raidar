"""``review-loop@1`` workflow agent (plan WS-C C4).

Ported from factory-x-loops ``harnesses/review_loop.py`` (``_drive``/``_run_stage``
/``parse_verdict``) as a Harbor agent that wraps the arm's own agent. One step
runs implement -> review -> [retry -> review]* inside the step's container,
each stage a fresh session of the wrapped agent (a new harness process):

- the reviewer sees the step instruction, a ``git diff --no-index`` of the
  workspace against the step's starting state, and the implementer's account;
- the loop stops on ``VERDICT: APPROVE``, on an unusable review (no verdict or
  empty feedback, recorded as a failed loop) or when the rounds run out;
- every stage's logs and ATIF trajectory are filed under
  ``/logs/agent/review-loop/stage_<n>_<role>/`` and embedded as
  ``subagent_trajectories`` in the step's root ``trajectory.json``;
- tokens and cost of every stage (reviewer sessions included) are summed into
  the step's ``AgentContext``, so the arm pays for its extra sessions; a field
  any stage did not report is left unavailable (None), never a partial sum.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, cast

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from harbor.models.trial.config import AgentConfig
from pydantic import BaseModel, ConfigDict, Field

from raidar.components.evidence import read_jsonl

APPROVE_LINE = "VERDICT: APPROVE"
CHANGES_LINE = "VERDICT: CHANGES"
APPROVE, CHANGES = "APPROVE", "CHANGES"
NO_VERDICT, EMPTY_FEEDBACK = "NO_VERDICT", "EMPTY_FEEDBACK"
UNUSABLE_VERDICTS = {NO_VERDICT: "review_no_verdict", EMPTY_FEEDBACK: "review_empty_feedback"}
LOOP_DIR_NAME = "review-loop"
LOOP_RECORD = "review-loop.json"
DEFAULT_WORK_DIR = "/tmp/raidar-review"
MAX_DIFF_CHARS = 200_000
# Files in /logs/agent that belong to the step, not to one stage.
STEP_LEVEL_LOGS = ("raidar-reset.json", "raidar-components.json", "raidar-components")
USAGE_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")

STAGE_PROMPTS = {
    "implement": """\
Complete the checkpoint specification below in the directory you are already in.

When the work is done, stop. A separate review pass runs next against your
working tree. Write a short account of what you changed and why to
`%%%REASONING_PATH%%%` — that file is outside the workspace on purpose, and the
reviewer reads it.

%%%TASK%%%
""",
    "review": """\
Another agent worked the checkpoint specification below, and a diff of its
changes follows. Its working tree is the directory you are already in: read the
code, run whatever you need, and treat the diff as where to start rather than as
the whole of the evidence. Its own account of the change should be at
`%%%REASONING_PATH%%%` — read that too, and weigh it as a claim rather than as
fact.

Judge only two things: does the change achieve the specification's stated aim,
and is the result maintainable (complexity, structure, naming, clarity). Ignore
subjective style preferences and nitpicks that bear on neither.

Change nothing yourself. You are reading, not editing — an edit from you would
corrupt what the next round's diff means.

End your reply with exactly one line: `VERDICT: APPROVE` if acceptable, or
`VERDICT: CHANGES` followed by the concrete, actionable feedback the implementer
needs to fix it.

%%%TASK%%%

The diff so far:

%%%DIFF%%%
""",
    "retry": """\
Complete the checkpoint specification below in the directory you are already in.

A review pass reads your work again when you stop, so stop before you would
otherwise call it finished. Your own account of last round's work is at
`%%%REASONING_PATH%%%`: read it first, then replace it with an account of the
round you are about to do.

%%%TASK%%%

A review of your last round found problems:

%%%FEEDBACK%%%
""",
}


def parse_verdict(text: str) -> tuple[str, str]:
    """The reviewer's ruling: the *last* marker wins (factory-x-loops semantics)."""
    approved, asked = text.rfind(APPROVE_LINE), text.rfind(CHANGES_LINE)
    if max(approved, asked) < 0:
        return NO_VERDICT, ""
    if approved > asked:
        return APPROVE, ""
    feedback = text[asked + len(CHANGES_LINE) :].strip()
    return (CHANGES, feedback) if feedback else (EMPTY_FEEDBACK, "")


def render(template: str, tokens: dict[str, str]) -> str:
    """Fill %%%TOKEN%%% placeholders; specs and diffs are full of braces."""
    for name, value in tokens.items():
        template = template.replace(f"%%%{name}%%%", value)
    return template


def _codex_final(path: Path) -> str:
    texts = [
        str(event["item"].get("text") or "")
        for event in read_jsonl(path)
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
    ]
    return texts[-1] if texts else ""


def _claude_final(path: Path) -> str:
    texts = [str(e.get("result") or "") for e in read_jsonl(path) if e.get("type") == "result"]
    return texts[-1] if texts else ""


def final_message(stage_dir: Path) -> str:
    """The stage's last assistant message, read back from its recorded stream."""
    codex, claude = stage_dir / "codex.txt", stage_dir / "claude-code.txt"
    if claude.is_file():
        return _claude_final(claude)
    return _codex_final(codex) if codex.is_file() else ""


class InnerAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Harbor agent name of the wrapped agent")
    kwargs: dict[str, Any] = Field(default_factory=dict)


class ReviewLoopOptions(AgentOptions):
    inner_agent: InnerAgent
    max_review_rounds: int = Field(default=3, ge=1, le=5)
    component: str = Field(default="review-loop@1")
    # Loop scratch outside the workspace (baseline tree, diffs, implementer's account).
    work_dir: str = Field(default=DEFAULT_WORK_DIR, pattern=r"^/[A-Za-z0-9._/-]+$")


@dataclass
class _Stage:
    index: int
    role: str
    round: int
    context: AgentContext
    directory: Path
    error: str | None = None

    @property
    def name(self) -> str:
        return f"stage_{self.index}_{self.role}"

    def record(self) -> dict[str, Any]:
        usage = {key: getattr(self.context, key) for key in USAGE_FIELDS}
        return {
            "index": self.index,
            "stage": self.role,
            "agent": "reviewer" if self.role == "review" else "implementer",
            "round": self.round,
            "stage_dir": f"{LOOP_DIR_NAME}/{self.name}",
            "error": self.error,
            **usage,
        }


@dataclass
class _LoopState:
    stages: list[_Stage] = field(default_factory=list)
    rounds: list[dict[str, str]] = field(default_factory=list)
    status: str = "failed"
    termination_reason: str = "loop_exception"
    error: str | None = None


def sum_usage(contexts: list[AgentContext]) -> dict[str, Any]:
    """Per-field totals over every stage; unavailable (None) if any stage lacks it.

    A partial sum would report some sessions' usage as the arm's whole cost, so
    an unknown stage makes the total unknown (unavailable is not zero).
    """
    totals: dict[str, Any] = {}
    for key in USAGE_FIELDS:
        values = [getattr(context, key) for context in contexts]
        totals[key] = sum(values) if values and None not in values else None
    return totals


def usage_gaps(stages: list[_Stage]) -> dict[str, list[str]]:
    """For each usage field, the stages that did not report it."""
    gaps = {
        key: [stage.name for stage in stages if getattr(stage.context, key) is None]
        for key in USAGE_FIELDS
    }
    return {key: names for key, names in gaps.items() if names}


class ReviewLoopAgent(BaseAgent):
    """Wraps a Harbor agent in fresh implement/review sessions (review-loop@1)."""

    options_model: ClassVar[type[AgentOptions] | None] = ReviewLoopOptions

    @staticmethod
    def name() -> str:
        return "raidar-review-loop"

    def version(self) -> str | None:
        return "1"

    @property
    def loop_options(self) -> ReviewLoopOptions:
        return cast(ReviewLoopOptions, self.options)

    def _inner(self, logs_dir: Path) -> BaseAgent:
        inner = self.loop_options.inner_agent
        return AgentFactory.create_agent_from_config(
            AgentConfig(
                name=inner.name,
                model_name=self.model_name,
                kwargs=inner.kwargs,
                env=dict(self._extra_env),
            ),
            logs_dir=logs_dir,
            logger=self.logger,
            mcp_servers=self.mcp_servers,
            skills_dir=self.skills_dir,
            environment_logs_dir=self.environment_logs_dir,
        )

    @property
    def work_dir(self) -> PurePosixPath:
        return PurePosixPath(self.loop_options.work_dir)

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = _LoopState()
        loop_dir = self.environment_logs_dir / LOOP_DIR_NAME
        await _exec_checked(
            environment,
            f"rm -rf {self.work_dir} && mkdir -p {self.work_dir}/trees "
            f"{shlex.quote(str(loop_dir))} && {_tree_copy_command(self.work_dir, 'baseline')}",
        )
        try:
            await self._drive(instruction, environment, state)
        except Exception as exc:
            state.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._finish(context, state)

    async def _drive(
        self, instruction: str, environment: BaseEnvironment, state: _LoopState
    ) -> None:
        feedback = ""
        for round_no in range(1, self.loop_options.max_review_rounds + 1):
            role = "implement" if round_no == 1 else "retry"
            tokens = {"TASK": instruction, "REASONING_PATH": str(self.work_dir / "reasoning.md")}
            tokens["FEEDBACK"] = feedback
            work = await self._stage(environment, state, role, round_no, tokens)
            if work.error is not None:
                state.termination_reason, state.error = "stage_failed", work.error
                return
            tokens["DIFF"] = await self._capture(environment, round_no) or "(no diff)"
            review = await self._stage(environment, state, "review", round_no, tokens)
            if review.error is not None:
                state.termination_reason, state.error = "stage_failed", review.error
                return
            verdict, feedback = parse_verdict(final_message(review.directory))
            state.rounds.append({"round": str(round_no), "verdict": verdict, "feedback": feedback})
            if verdict == APPROVE:
                state.status, state.termination_reason = "completed", "review_approved"
                return
            if verdict in UNUSABLE_VERDICTS:
                state.termination_reason = UNUSABLE_VERDICTS[verdict]
                return
        state.status, state.termination_reason = "completed", "rounds_exhausted"

    async def _stage(
        self,
        environment: BaseEnvironment,
        state: _LoopState,
        role: str,
        round_no: int,
        tokens: dict[str, str],
    ) -> _Stage:
        index = len(state.stages) + 1
        stage = _Stage(index, role, round_no, AgentContext(), Path())
        stage.directory = self.logs_dir / LOOP_DIR_NAME / stage.name
        state.stages.append(stage)
        inner = self._inner(stage.directory)
        try:
            await inner.run(render(STAGE_PROMPTS[role], tokens), environment, stage.context)
        except Exception as exc:
            stage.error = f"{type(exc).__name__}: {exc}"
        await self._file_stage_logs(environment, stage)
        if stage.context.is_empty():
            inner.populate_context_post_run(stage.context)
        return stage

    async def _file_stage_logs(self, environment: BaseEnvironment, stage: _Stage) -> None:
        logs = shlex.quote(str(self.environment_logs_dir))
        target = f"{LOOP_DIR_NAME}/{stage.name}"
        keep = " ".join(
            f"! -name {shlex.quote(name)}" for name in (LOOP_DIR_NAME, *STEP_LEVEL_LOGS)
        )
        await _exec_checked(
            environment,
            f"cd {logs} && mkdir -p {target} && "
            f"find . -mindepth 1 -maxdepth 1 {keep} -exec mv {{}} {target}/ \\;",
        )
        if not environment.capabilities.mounted:
            await environment.download_dir(str(self.environment_logs_dir / target), stage.directory)

    async def _capture(self, environment: BaseEnvironment, round_no: int) -> str:
        patch = self.environment_logs_dir / LOOP_DIR_NAME / "diffs" / f"round_{round_no}.patch"
        reasoning = self.environment_logs_dir / LOOP_DIR_NAME / f"reasoning_round_{round_no}.md"
        tree, work = f"round_{round_no}", self.work_dir
        account = work / "reasoning.md"
        await _exec_checked(
            environment,
            f"rm -rf {work}/trees/{tree} && {_tree_copy_command(work, tree)} && "
            f"mkdir -p {shlex.quote(str(patch.parent))} && cd {work}/trees && "
            f"{{ git diff --no-index --no-color baseline {tree} > {shlex.quote(str(patch))}; "
            "rc=$?; [ $rc -le 1 ]; } && "
            f"{{ [ ! -f {account} ] || cp {account} {shlex.quote(str(reasoning))}; }}",
        )
        result = await environment.exec(f"head -c {MAX_DIFF_CHARS + 1} {shlex.quote(str(patch))}")
        diff = result.stdout or ""
        if len(diff) > MAX_DIFF_CHARS:
            diff = (
                diff[:MAX_DIFF_CHARS] + f"\n... diff truncated at {MAX_DIFF_CHARS} characters ...\n"
            )
        return diff

    def _finish(self, context: AgentContext, state: _LoopState) -> None:
        stage_contexts = [stage.context for stage in state.stages]
        for key, value in sum_usage(stage_contexts).items():
            setattr(context, key, value)
        record = {
            "workflow": self.loop_options.component,
            "status": state.status,
            "termination_reason": state.termination_reason,
            "error": state.error,
            "rounds_configured": self.loop_options.max_review_rounds,
            "rounds_used": len(state.rounds),
            "final_verdict": state.rounds[-1]["verdict"] if state.rounds else None,
            "verdicts": state.rounds,
            "stages": [stage.record() for stage in state.stages],
            "usage_unavailable": usage_gaps(state.stages),
        }
        context.metadata = {**(context.metadata or {}), "review_loop": record}
        loop_dir = self.logs_dir / LOOP_DIR_NAME
        loop_dir.mkdir(parents=True, exist_ok=True)
        (loop_dir / LOOP_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        trajectory = self._root_trajectory(state)
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), indent=2) + "\n"
        )

    def _root_trajectory(self, state: _LoopState) -> Trajectory:
        subagents: list[Trajectory] = []
        steps: list[dict[str, Any]] = []
        for stage in state.stages:
            ref: dict[str, Any] = {"trajectory_id": stage.name}
            path = stage.directory / "trajectory.json"
            if path.is_file():
                sub = Trajectory.model_validate_json(path.read_text())
                subagents.append(sub.model_copy(update={"trajectory_id": stage.name}))
            else:
                ref = {
                    "trajectory_path": f"{LOOP_DIR_NAME}/{stage.name}",
                    "extra": {"missing": True},
                }
            steps.append(
                {
                    "step_id": len(steps) + 1,
                    "source": "system",
                    "message": f"review-loop {stage.name} (round {stage.round})",
                    "observation": {
                        "results": [{"content": stage.error, "subagent_trajectory_ref": [ref]}]
                    },
                }
            )
        usage = sum_usage([stage.context for stage in state.stages])
        return Trajectory.model_validate(
            {
                "session_id": self.session_id,
                "agent": {"name": self.name(), "version": "1", "model_name": self.model_name},
                "steps": steps
                or [{"step_id": 1, "source": "system", "message": "review-loop ran no stage"}],
                "final_metrics": {
                    "total_prompt_tokens": usage["n_input_tokens"],
                    "total_completion_tokens": usage["n_output_tokens"],
                    "total_cached_tokens": usage["n_cache_tokens"],
                    "total_cost_usd": usage["cost_usd"],
                    "total_steps": len(state.stages),
                },
                "subagent_trajectories": subagents or None,
            }
        )


def _tree_copy_command(work_dir: PurePosixPath, tree: str) -> str:
    """Copy the workspace (the exec working directory) into a comparison tree."""
    return (
        f"mkdir -p {work_dir}/trees/{tree} && tar --exclude=__pycache__ "
        f"--exclude='*.pyc' -cf - . | tar -C {work_dir}/trees/{tree} -xf -"
    )


async def _exec_checked(environment: BaseEnvironment, command: str) -> None:
    result = await environment.exec(command)
    if result.return_code != 0:
        raise RuntimeError(
            f"review-loop command failed ({result.return_code}): {(result.stderr or '')[-500:]}"
        )
