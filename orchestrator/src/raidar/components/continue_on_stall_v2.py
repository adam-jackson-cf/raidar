"""``continue-on-stall@2`` session wrapper: @1 with refusal patterns and one continuation per step.

``continue-on-stall@1`` (``raidar.components.continue_on_stall``) is released
and unchanged. @2 keeps its detection, continuation, log filing, usage sums,
record and root trajectory, with two changes:

- **no-edit refusals stall too.** A session that changed nothing and ended
  saying it cannot proceed without something ("I can't ... without", "not
  enough context", "... isn't defined/present") is a stall like a question:
  @1 missed both no-edit steps of ``smoke-03`` r2 ("I can't build a compatible
  ``forge`` CLI without that context"). The patterns are fixed and
  problem-agnostic (``REFUSAL_PATTERNS``), checked beside @1's request
  patterns; curly apostrophes count as apostrophes;
- **at most one continuation per step, for the step's first session.** @1 ran
  around every session, so inside a gate with fix rounds a step could be
  continued up to once per session, while an ungated (or shadow-gated) arm's
  step had one session: unequal exposure across arms. @2 decides eligibility
  from the step's own logs: a session is eligible only when no earlier
  continue-on-stall record exists under the step's agent logs dir (the
  execution layer empties it between steps; a workflow wrapper files each
  session's logs, record included, under it). The step's first session is
  therefore the only one that can stall, in every arm. A later session is
  still observed and recorded (``eligible: false``, ``would_stall``), never
  continued, and its ``stalled`` is false.

Nothing in it names a problem, a harness-specific prompt or a gate.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.components.continue_on_stall import (
    ASSUMPTIONS_FILE,
    MESSAGE_EXCERPT_CHARS,
    REQUEST_PATTERNS,
    STALL_RECORD,
    STALLED_SESSION_DIR,
    ContinueOnStallAgent,
    ContinueOnStallOptions,
    _Observation,
    _State,
)
from raidar.components.review_loop import PROMPT_ARG_BUDGET, render
from raidar.components.session_chain import render_task

COMPONENT = "continue-on-stall@2"
VERSION = "2"
_APOSTROPHE = "['’]"

CONTINUE_PROMPT = """\
You stopped without changing any file: you asked for input, or said you could not
proceed without more information. No human is available: nobody will answer
questions, confirm choices or supply anything more, now or later, so do not ask again.

Proceed now with the task below, in the directory you are already in. Where the
specification leaves something open or missing, make a reasonable assumption,
record each assumption (one per line, with the reason) in %%%ASSUMPTIONS%%% in that
directory, and complete the work.

%%%TASK%%%

Your last message was:

%%%MESSAGE%%%
"""

# A final message "refuses": it says the work cannot proceed without something, or
# that something it needs is missing. Fixed patterns, the same for every problem.
REFUSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "cannot-without",
        re.compile(
            rf"\b(?:i|we)\s+(?:can{_APOSTROPHE}?t|cannot|can not|(?:am|are) unable to|"
            rf"(?:am|are) not able to|won{_APOSTROPHE}t be able to)\b[^.!?\n]{{0,160}}"
            r"\bwithout\b",
            re.IGNORECASE,
        ),
    ),
    (
        "missing-information",
        re.compile(
            r"\b(?:not enough|insufficient|missing|lacks?|lacking)\s+(?:the\s+|any\s+)?"
            r"(?:context|information|details?|requirements|specification)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "not-defined",
        re.compile(
            rf"\b(?:is|are){_APOSTROPHE}?n{_APOSTROPHE}?t\s+(?:present|defined|specified|"
            rf"provided|available)\b|\b(?:is|are)\s+not\s+(?:present|defined|specified|"
            rf"provided|available)\b|\b(?:does|do){_APOSTROPHE}?n{_APOSTROPHE}?t\s+"
            rf"(?:define|specify|say)\b|\b(?:does|do)\s+not\s+(?:define|specify|say)\b",
            re.IGNORECASE,
        ),
    ),
)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
PATTERNS = (*REQUEST_PATTERNS, *REFUSAL_PATTERNS)


def detect_stall_message(message: str) -> tuple[str, ...]:
    """@1's request patterns and @2's refusal patterns ``message`` matches, in order."""
    text = _FENCE.sub(" ", message)
    return tuple(name for name, pattern in PATTERNS if pattern.search(text))


def continuation_prompt(task: str, message: str) -> str:
    excerpt = message.strip()[:MESSAGE_EXCERPT_CHARS] or "(no message)"
    tokens = {"TASK": task, "MESSAGE": excerpt, "ASSUMPTIONS": ASSUMPTIONS_FILE}
    prompt = render(CONTINUE_PROMPT, tokens)
    if len(prompt.encode()) <= PROMPT_ARG_BUDGET:
        return prompt
    return render(CONTINUE_PROMPT, {**tokens, "MESSAGE": excerpt[:200]})


@dataclass
class _StateV2(_State):
    eligible: bool = True

    @property
    def would_stall(self) -> bool:
        first = self.first
        return first is not None and first.changed is False and bool(first.matched)

    @property
    def stalled(self) -> bool:
        return self.eligible and self.would_stall


class ContinueOnStallV2Options(ContinueOnStallOptions):
    component: str = COMPONENT


class ContinueOnStallV2Agent(ContinueOnStallAgent):
    """@1's wrapper: refusals stall too; only the step's first session can be continued."""

    options_model = ContinueOnStallV2Options

    @staticmethod
    def name() -> str:
        return "raidar-continue-on-stall"

    def version(self) -> str | None:
        return VERSION

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._state = state = _StateV2()
        task = render_task(self.stall_options.inner_agent, instruction)
        state.eligible = not await self._earlier_record(environment)
        before = await self._names(environment)
        state.first = _Observation(before=await self._fingerprint(environment))
        try:
            await self._run_native(environment, state, "session", task)
            await self._observe(environment, state.first)
            if state.stalled:
                await self._file_stalled_logs(environment, before)
                state.runs[0].logs = STALLED_SESSION_DIR
                state.continuation = _Observation(before=state.first.after or "")
                await self._run_native(
                    environment,
                    state,
                    "continuation",
                    continuation_prompt(task, state.first.message),
                )
                await self._observe(environment, state.continuation)
        finally:
            state.record = self._record(state)
            await self._write_record(environment, state.record)
            if all(not run.context.is_empty() for run in state.runs):
                self._sum_into(context)

    async def _earlier_record(self, environment: BaseEnvironment) -> bool:
        """Whether this step's logs already hold a continue-on-stall record (a later session)."""
        logs = shlex.quote(str(self.environment_logs_dir))
        result = await environment.exec(
            f"find {logs} -name {shlex.quote(STALL_RECORD)} -type f -print -quit 2>/dev/null"
        )
        return bool((result.stdout or "").strip())

    async def _observe(self, environment: BaseEnvironment, seen: _Observation) -> None:
        await super()._observe(environment, seen)
        seen.matched = detect_stall_message(seen.message)

    def _record(self, state: _State) -> dict[str, Any]:
        record = super()._record(state)
        v2 = state if isinstance(state, _StateV2) else _StateV2()
        record["eligible"] = v2.eligible
        record["would_stall"] = v2.would_stall
        return record
