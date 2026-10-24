"""``review-loop@2`` workflow agent.

A Harbor agent that wraps the arm's own agent. One step
runs implement -> review -> [retry -> review]* inside the step's container,
each stage a fresh session of the wrapped agent (a new harness process):

- the reviewer sees the step instruction, a ``git diff --no-index`` of the
  workspace against the step's starting state, and the implementer's account;
- a verdict marker may carry markdown emphasis or backticks. A marker that
  starts a line is the reviewer's ruling and the last such marker wins; a
  marker quoted mid-sentence after it cannot overturn it. Only a reply with no
  line-start marker falls back to the last marker anywhere;
- for ``CHANGES`` the feedback is the reply's text before *and* after that
  line, so any non-empty feedback makes the verdict actionable;
- an unusable review (no marker, or ``CHANGES`` with nothing else) gets one
  re-ask: a fresh reviewer session with the same task and diff plus the
  unusable reply. If that is unusable too, the loop stops with the work as it
  stands and records ``review_unusable_after_reask``; it also stops on
  ``VERDICT: APPROVE`` or when the rounds run out;
- every parse is recorded in ``review-loop.json`` under ``parses``: round,
  attempt, stage, verdict, where the feedback sat, and the number of markers;
- every stage's logs and ATIF trajectory are filed under
  ``/logs/agent/review-loop/stage_<n>_<role>/`` and embedded as
  ``subagent_trajectories`` in the step's root ``trajectory.json``;
- tokens and cost of every stage (reviewer sessions included) are summed into
  the step's ``AgentContext``, so the arm pays for its extra sessions; a field
  any stage did not report is left unavailable (None), never a partial sum;
- a stage whose inner agent raised ends the loop, and the loop then re-raises
  that exception after writing its record, so Harbor and ``execution.collect``
  classify it exactly as for the unwrapped agent (an API error is infra in both
  arms; a context-window overflow is graded in both);
- a stage cancelled by the agent timeout still files its logs and reports its
  usage before the cancellation propagates.

A ``prompt_template_path`` on the wrapped agent (``prompt-wrapper@1``) is
applied by the loop to the step instruction for the implementer's stages only.
Harbor would otherwise wrap every stage prompt, telling the reviewer to build
the program. The reviewer judges against the bare step instruction. The wrapped
agent may be a session wrapper given by ``import_path``
(``raidar.components.runtime.session_chain``), which runs every stage.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, cast

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from pydantic import Field

from raidar.components.definition.evidence import read_jsonl
from raidar.components.runtime.session_chain import InnerAgentSpec, create_inner, render_task
from raidar.core.snapshot import SNAPSHOT_EXCLUDES

COMPONENT = "review-loop@2"
VERSION = "2"
APPROVE, CHANGES = "APPROVE", "CHANGES"
NO_VERDICT, EMPTY_FEEDBACK = "NO_VERDICT", "EMPTY_FEEDBACK"
UNUSABLE = (NO_VERDICT, EMPTY_FEEDBACK)
FALLBACK_REASON = "review_unusable_after_reask"
# The unusable reply quoted back to the re-asked reviewer (gpt-6-luna's run to ~2 KB).
MAX_PREVIOUS_REVIEW_CHARS = 20_000
# A verdict marker, wherever it sits, with optional markdown emphasis or backticks
# around it. ``**VERDICT: CHANGES**`` at the end of a paragraph counts (pilot b04
# checkpoint_4).
_DECORATION = "*_`"
MARKER_RE = re.compile(r"[*_`]*VERDICT:[ \t*_`]*(?P<verdict>APPROVE|CHANGES)\b[*_`]*")
LOOP_DIR_NAME = "review-loop"
LOOP_RECORD = "review-loop.json"
DEFAULT_WORK_DIR = "/tmp/raidar-review"
MAX_DIFF_CHARS = 200_000
# A harness session gets its prompt as one shell-quoted argument of a ``bash -c``
# command, and Linux caps a single argument at MAX_ARG_STRLEN (128 KiB): a larger
# prompt fails with "argument list too long" (exit 255). The quoted review prompt
# is kept under this budget, leaving room for the harness's own command text.
PROMPT_ARG_BUDGET = 112 * 1024
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

REASK_PROMPT = """\
Another agent worked the checkpoint specification below, and a diff of its
changes follows. Its working tree is the directory you are already in. Its own
account of the change should be at `%%%REASONING_PATH%%%`.

An earlier review of this same work ended without a usable verdict line. Its
reply is quoted below. Use it, check anything you need in the working tree, and
give the ruling.

Judge only two things: does the change achieve the specification's stated aim,
and is the result maintainable (complexity, structure, naming, clarity).

Change nothing yourself.

End your reply with exactly one line: `VERDICT: APPROVE` if acceptable, or
`VERDICT: CHANGES` followed by the concrete, actionable feedback the implementer
needs to fix it.

%%%TASK%%%

The earlier review's reply:

%%%PREVIOUS_REVIEW%%%

The diff so far:

%%%DIFF%%%
"""


@dataclass(frozen=True)
class ReviewParse:
    """One reviewer reply, read: the verdict, its feedback and how it was found."""

    verdict: str
    feedback: str
    feedback_position: str  # "before", "after", "both" or "none"
    markers: int

    def record(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "feedback_position": self.feedback_position,
            "feedback_chars": len(self.feedback),
            "markers": self.markers,
        }


def _starts_line(text: str, match: re.Match[str]) -> bool:
    line_start = text.rfind("\n", 0, match.start()) + 1
    return not text[line_start : match.start()].strip(f"{_DECORATION} \t>-")


def _ruling(text: str, markers: list[re.Match[str]]) -> re.Match[str]:
    """The last line-start marker, else (none starts a line) the last marker anywhere."""
    rulings = [match for match in markers if _starts_line(text, match)]
    return (rulings or markers)[-1]


def parse_review(text: str) -> ReviewParse:
    """The reviewer's ruling; feedback may sit before or after the ruling marker."""
    markers = list(MARKER_RE.finditer(text))
    if not markers:
        return ReviewParse(NO_VERDICT, "", "none", 0)
    last = _ruling(text, markers)
    if last["verdict"] == APPROVE:
        return ReviewParse(APPROVE, "", "none", len(markers))
    before = text[: last.start()].strip()
    after = text[last.end() :].lstrip(f"{_DECORATION}:—–- \t").strip()
    parts = {"before": before, "after": after}
    present = [name for name, part in parts.items() if part]
    if not present:
        return ReviewParse(EMPTY_FEEDBACK, "", "none", len(markers))
    position = "both" if len(present) == 2 else present[0]
    feedback = "\n\n".join(parts[name] for name in present)
    return ReviewParse(CHANGES, feedback, position, len(markers))


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... reply truncated at {limit} characters ...\n"


def quoted_size(text: str) -> int:
    """Bytes ``text`` takes inside a single-quoted shell word (' becomes '"'"')."""
    return len(text.encode()) + 4 * text.count("'")


def fit_diff(template: str, tokens: dict[str, str], diff: str) -> str:
    """``diff``, cut so the rendered prompt stays within ``PROMPT_ARG_BUDGET``."""
    budget = PROMPT_ARG_BUDGET - quoted_size(render(template, {**tokens, "DIFF": ""})) - 2
    if quoted_size(diff) <= budget:
        return diff
    marker = "\n... diff truncated to fit the harness prompt limit ...\n"
    budget -= quoted_size(marker)
    used = 0
    for index, char in enumerate(diff):
        used += quoted_size(char)
        if used > budget:
            return diff[:index] + marker
    return diff  # pragma: no cover - the loop returns once over budget


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


class ReviewLoopOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    max_review_rounds: int = Field(default=3, ge=1, le=5)
    component: str = Field(default=COMPONENT)
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


def usage_gaps(named: Iterable[tuple[str, AgentContext]]) -> dict[str, list[str]]:
    """For each usage field, the sessions (name, context) that did not report it."""
    pairs = list(named)
    gaps = {
        key: [name for name, context in pairs if getattr(context, key) is None]
        for key in USAGE_FIELDS
    }
    return {key: names for key, names in gaps.items() if names}


class ReviewLoopAgent(BaseAgent):
    """Wraps a Harbor agent in fresh implement/review sessions: position-tolerant verdicts,
    one re-ask of an unusable review, recorded parses."""

    options_model: ClassVar[type[AgentOptions] | None] = ReviewLoopOptions

    @staticmethod
    def name() -> str:
        return "raidar-review-loop"

    def version(self) -> str | None:
        return VERSION

    @property
    def loop_options(self) -> ReviewLoopOptions:
        return cast(ReviewLoopOptions, self.options)

    def _implementer_task(self, instruction: str) -> str:
        return render_task(self.loop_options.inner_agent, instruction)

    def _inner(self, logs_dir: Path) -> BaseAgent:
        """The wrapped agent without its prompt template: the loop applies that itself."""
        return create_inner(
            self, self.loop_options.inner_agent, logs_dir, model_name=self.model_name
        )

    @property
    def work_dir(self) -> PurePosixPath:
        return PurePosixPath(self.loop_options.work_dir)

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._parses: list[dict[str, Any]] = []
        self._failure: Exception | None = None
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
        if self._failure is not None:
            # Classified by Harbor/collect exactly as the unwrapped agent's error.
            raise self._failure

    async def _drive(
        self, instruction: str, environment: BaseEnvironment, state: _LoopState
    ) -> None:
        feedback = ""
        reasoning = str(self.work_dir / "reasoning.md")
        for round_no in range(1, self.loop_options.max_review_rounds + 1):
            role = "implement" if round_no == 1 else "retry"
            work_tokens = {
                "TASK": self._implementer_task(instruction),
                "REASONING_PATH": reasoning,
                "FEEDBACK": feedback,
            }
            work = await self._stage(
                environment, state, role, round_no, render(STAGE_PROMPTS[role], work_tokens)
            )
            if work.error is not None:
                state.termination_reason, state.error = "stage_failed", work.error
                return
            diff = await self._capture(environment, round_no) or "(no diff)"
            outcome = await self._review(environment, state, round_no, instruction, diff)
            if outcome is None:
                return
            state.rounds.append(
                {"round": str(round_no), "verdict": outcome.verdict, "feedback": outcome.feedback}
            )
            if outcome.verdict == APPROVE:
                state.status, state.termination_reason = "completed", "review_approved"
                return
            if outcome.verdict in UNUSABLE:
                state.status, state.termination_reason = "completed", FALLBACK_REASON
                return
            feedback = outcome.feedback
        state.status, state.termination_reason = "completed", "rounds_exhausted"

    async def _review(
        self,
        environment: BaseEnvironment,
        state: _LoopState,
        round_no: int,
        instruction: str,
        diff: str,
    ) -> ReviewParse | None:
        """Review once, re-ask once if unusable; ``None`` when a stage failed."""
        tokens = {"TASK": instruction, "REASONING_PATH": str(self.work_dir / "reasoning.md")}
        template = STAGE_PROMPTS["review"]
        previous = ""
        for attempt in (1, 2):
            if attempt == 2:
                template = REASK_PROMPT
                tokens["PREVIOUS_REVIEW"] = _clip(previous, MAX_PREVIOUS_REVIEW_CHARS) or "(empty)"
            prompt = render(template, {**tokens, "DIFF": fit_diff(template, tokens, diff)})
            review = await self._stage(environment, state, "review", round_no, prompt)
            if review.error is not None:
                state.termination_reason, state.error = "stage_failed", review.error
                return None
            previous = final_message(review.directory)
            parsed = parse_review(previous)
            self._parses.append(
                {"round": round_no, "attempt": attempt, "stage": review.name, **parsed.record()}
            )
            if parsed.verdict not in UNUSABLE:
                return parsed
        return parsed

    async def _stage(
        self,
        environment: BaseEnvironment,
        state: _LoopState,
        role: str,
        round_no: int,
        prompt: str,
    ) -> _Stage:
        index = len(state.stages) + 1
        stage = _Stage(index, role, round_no, AgentContext(), Path())
        stage.directory = self.logs_dir / LOOP_DIR_NAME / stage.name
        state.stages.append(stage)
        inner = self._inner(stage.directory)
        try:
            await inner.run(prompt, environment, stage.context)
        except asyncio.CancelledError:
            stage.error = "CancelledError: the agent phase was cancelled (agent timeout)"
            await asyncio.shield(self._settle_stage(environment, stage, inner))
            raise
        except Exception as exc:
            stage.error = f"{type(exc).__name__}: {exc}"
            if self._failure is None:
                self._failure = exc
        await self._settle_stage(environment, stage, inner)
        return stage

    async def _settle_stage(
        self, environment: BaseEnvironment, stage: _Stage, inner: BaseAgent
    ) -> None:
        """File the stage's logs and read its usage (also after a cancellation)."""
        await self._file_stage_logs(environment, stage)
        if stage.context.is_empty():
            inner.populate_context_post_run(stage.context)

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
            "usage_unavailable": usage_gaps((s.name, s.context) for s in state.stages),
            "parses": self._parses,
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
                "agent": {"name": self.name(), "version": VERSION, "model_name": self.model_name},
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
    """Copy the workspace (the exec working directory) into a comparison tree.

    It leaves out what the graded snapshot leaves out (``SNAPSHOT_EXCLUDES``):
    a ``.venv`` from ``uv run`` otherwise filled the reviewer's diff (pilot).
    """
    excludes = " ".join(f"--exclude={shlex.quote(name)}" for name in (*SNAPSHOT_EXCLUDES, "*.pyc"))
    return (
        f"mkdir -p {work_dir}/trees/{tree} && tar {excludes} "
        f"-cf - . | tar -C {work_dir}/trees/{tree} -xf -"
    )


async def _exec_checked(environment: BaseEnvironment, command: str) -> None:
    result = await environment.exec(command)
    if result.return_code != 0:
        raise RuntimeError(
            f"review-loop command failed ({result.return_code}): {(result.stderr or '')[-500:]}"
        )
