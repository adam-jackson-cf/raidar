"""``review-loop@2`` workflow agent: review-loop@1 with a model-tolerant verdict.

review-loop@1 (``raidar.components.runtime.review_loop``) is locked: its parser takes
feedback only *after* ``VERDICT: CHANGES`` and ends the loop on any unusable
reply. On the pilot, gpt-6-luna wrote its feedback *before* the verdict line,
so 18 of 40 forge reviews parsed as ``EMPTY_FEEDBACK`` and the fix round never
ran (gate packet F3). review-loop@2 keeps @1's stages, prompts, diff capture,
log filing and billing, and changes only how a review is read:

- a verdict marker may carry markdown emphasis or backticks. A marker that
  starts a line is the reviewer's ruling and the last such marker wins; a
  marker quoted mid-sentence after it ("... it would be VERDICT: APPROVE")
  cannot overturn it. Only a reply with no line-start marker falls back to
  @1's rule (the last marker anywhere, e.g. ``... **VERDICT: CHANGES**``);
- for ``CHANGES`` the feedback is the reply's text before *and* after that
  line, so any non-empty feedback makes the verdict actionable;
- an unusable review (no marker, or ``CHANGES`` with nothing else) gets one
  re-ask: a fresh reviewer session with the same task and diff plus the
  unusable reply. If that is unusable too, the documented fallback applies:
  the loop stops with the work as it stands (no fix round on feedback that
  does not exist) and records ``review_unusable_after_reask``;
- every parse is recorded in ``review-loop.json`` under ``parses``: round,
  attempt, stage, verdict, where the feedback sat, and the number of markers;
- a stage whose inner agent raised ends the loop as in @1, and the loop then
  re-raises that exception after writing its record, so Harbor and
  ``execution.collect`` classify it exactly as for the unwrapped agent (an API
  error is infra in both arms; a context-window overflow is graded in both);
- a stage cancelled by the agent timeout still files its logs and reports its
  usage before the cancellation propagates.

A ``prompt_template_path`` on the wrapped agent (``prompt-wrapper@1``) is
applied by the loop to the step instruction for the implementer's stages only.
Harbor would otherwise wrap every stage prompt, telling the reviewer to build
the program. The reviewer judges against the bare step instruction.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from harbor.models.trial.config import AgentConfig
from harbor.utils.templating import render_prompt_template
from pydantic import Field

from raidar.components.runtime.review_loop import (
    APPROVE,
    CHANGES,
    EMPTY_FEEDBACK,
    LOOP_DIR_NAME,
    LOOP_RECORD,
    NO_VERDICT,
    STAGE_PROMPTS,
    ReviewLoopAgent,
    ReviewLoopOptions,
    _LoopState,
    _Stage,
    final_message,
    fit_diff,
    render,
)

COMPONENT = "review-loop@2"
VERSION = "2"
UNUSABLE = (NO_VERDICT, EMPTY_FEEDBACK)
FALLBACK_REASON = "review_unusable_after_reask"
PROMPT_TEMPLATE_KWARG = "prompt_template_path"
# The unusable reply quoted back to the re-asked reviewer (gpt-6-luna's run to ~2 KB).
MAX_PREVIOUS_REVIEW_CHARS = 20_000
# A verdict marker, wherever it sits (as in @1), with optional markdown emphasis
# or backticks around it. ``**VERDICT: CHANGES**`` at the end of a paragraph
# counts (pilot b04 checkpoint_4).
_DECORATION = "*_`"
MARKER_RE = re.compile(r"[*_`]*VERDICT:[ \t*_`]*(?P<verdict>APPROVE|CHANGES)\b[*_`]*")

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


class ReviewLoopV2Options(ReviewLoopOptions):
    component: str = Field(default=COMPONENT)


class ReviewLoopV2Agent(ReviewLoopAgent):
    """review-loop@2: position-tolerant verdicts, one re-ask, recorded parses."""

    options_model: ClassVar[type[ReviewLoopOptions] | None] = ReviewLoopV2Options

    def version(self) -> str | None:
        return VERSION

    @property
    def prompt_template(self) -> Path | None:
        value = self.loop_options.inner_agent.kwargs.get(PROMPT_TEMPLATE_KWARG)
        return Path(str(value)) if value else None

    def _inner(self, logs_dir: Path) -> BaseAgent:
        """The wrapped agent without its prompt template: the loop applies that itself."""
        inner = self.loop_options.inner_agent
        kwargs = {key: v for key, v in inner.kwargs.items() if key != PROMPT_TEMPLATE_KWARG}
        return AgentFactory.create_agent_from_config(
            AgentConfig(
                name=inner.name,
                model_name=self.model_name,
                kwargs=kwargs,
                env=dict(self._extra_env),
            ),
            logs_dir=logs_dir,
            logger=self.logger,
            mcp_servers=self.mcp_servers,
            skills_dir=self.skills_dir,
            environment_logs_dir=self.environment_logs_dir,
        )

    def _implementer_task(self, instruction: str) -> str:
        template = self.prompt_template
        return render_prompt_template(template, instruction) if template else instruction

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._parses: list[dict[str, Any]] = []
        self._failure: Exception | None = None
        await super().run(instruction, environment, context)
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
            work = await self._prompted_stage(
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
            review = await self._prompted_stage(environment, state, "review", round_no, prompt)
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

    async def _prompted_stage(
        self,
        environment: BaseEnvironment,
        state: _LoopState,
        role: str,
        round_no: int,
        prompt: str,
    ) -> _Stage:
        """review-loop@1's ``_stage`` with the rendered prompt passed in."""
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

    def _finish(self, context: AgentContext, state: _LoopState) -> None:
        """review-loop@1's record and trajectory, plus every verdict parse."""
        super()._finish(context, state)
        record = {**(context.metadata or {})["review_loop"], "parses": self._parses}
        context.metadata = {**(context.metadata or {}), "review_loop": record}
        (self.logs_dir / LOOP_DIR_NAME / LOOP_RECORD).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )

    def _root_trajectory(self, state: _LoopState) -> Trajectory:
        trajectory = super()._root_trajectory(state)
        agent = trajectory.agent.model_copy(update={"version": VERSION})
        return trajectory.model_copy(update={"agent": agent})
