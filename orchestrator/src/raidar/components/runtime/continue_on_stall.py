"""``continue-on-stall@1`` session wrapper: no human will answer, so a stalled session continues.

An agent sometimes ends a session by asking a question without changing
anything. No human is there to answer, so the step would end with no work.
This wrapper runs each session of the native agent and, when the session

- changed no file in the workspace (``/app``: the tree with the verifier's
  snapshot excludes, hashed before and after the session), **and**
- ended with a final message that asks a question or asks for clarification
  or confirmation (``detect_request``: fixed, problem-agnostic patterns),

starts **one** continuation session of the same agent: the task again, its own
last message, and the statement that no human is available, that it must
proceed with the specification and reasonable assumptions, and record each
assumption in ``ASSUMPTIONS.md`` in the workspace. Detection is deterministic
and recorded for every session, stalled or not:
``/logs/agent/continue-on-stall/continue-on-stall.json`` (the fingerprints,
whether anything changed, the final message's opening, the matched patterns,
``stalled``, ``continued`` and the continuation's outcome). Inside a workflow
wrapper (``guardrail-gate@3``) the record is filed with that session's logs,
so a step has one record per session.

The stalled session's logs are moved to ``continue-on-stall/stalled_session``
before the continuation runs; both sessions' tokens and cost are summed into
the session's context (unknown if either did not report, never a partial sum)
and, after a continuation, the session's ``trajectory.json`` is a root
trajectory embedding both. The wrapper is harness-agnostic (it reads Codex's
``codex.txt`` or Claude Code's ``claude-code.txt`` for the final message) and
names nothing problem-specific.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from pydantic import Field

from raidar.components.runtime.review_loop import (
    PROMPT_ARG_BUDGET,
    USAGE_FIELDS,
    final_message,
    render,
    sum_usage,
)
from raidar.components.runtime.session_chain import InnerAgentSpec, create_inner, render_task
from raidar.core.snapshot import APP_SOURCE, SNAPSHOT_EXCLUDES

COMPONENT = "continue-on-stall@1"
VERSION = "1"
STALL_DIR = "continue-on-stall"
STALL_RECORD = "continue-on-stall.json"
STALLED_SESSION_DIR = "stalled_session"
ASSUMPTIONS_FILE = "ASSUMPTIONS.md"
MESSAGE_EXCERPT_CHARS = 2000
MESSAGE_FILES = ("codex.txt", "claude-code.txt")
MESSAGE_TAIL_BYTES = 4_000_000

CONTINUE_PROMPT = """\
You stopped without changing any file and asked for input. No human is available:
nobody will answer questions or confirm choices, now or later, so do not ask again.

Proceed now with the task below, in the directory you are already in. Where the
specification leaves something open, make a reasonable assumption, record each
assumption (one per line, with the reason) in %%%ASSUMPTIONS%%% in that directory,
and complete the work.

%%%TASK%%%

Your last message was:

%%%MESSAGE%%%
"""

# A final message "asks": a sentence ending in "?", or a request for clarification,
# confirmation or input. Fixed patterns, the same for every problem and harness.
REQUEST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # A "?" that ends a sentence: followed by whitespace, the end, or closing marks
    # (never a URL query or code such as ``a?b``, which a non-space follows).
    ("question", re.compile(r"\?(?=[\"')\]*_`]*(?:\s|$))")),
    (
        "clarify-confirm",
        re.compile(
            r"\b(?:please|could you|can you|would you|kindly)\s+(?:clarify|confirm|specify|"
            r"provide|tell me|let me know|advise)\b",
            re.IGNORECASE,
        ),
    ),
    ("let-me-know", re.compile(r"\blet me know\b", re.IGNORECASE)),
    (
        "needs-input",
        re.compile(
            r"\b(?:need|needs|require|requires|awaiting|waiting for)\s+(?:your\s+|more\s+)?"
            r"(?:clarification|confirmation|input|guidance|decision|answer|details|information)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "before-proceeding",
        re.compile(r"\bbefore (?:i|we) (?:proceed|continue|start|implement)\b", re.IGNORECASE),
    ),
    (
        "offer-choice",
        re.compile(
            r"\b(?:should i|shall i|do you want|would you like|which (?:option|one|approach)|"
            r"what would you prefer)\b",
            re.IGNORECASE,
        ),
    ),
)
_FENCE = re.compile(r"```.*?```", re.DOTALL)


def detect_request(message: str) -> tuple[str, ...]:
    """The request patterns ``message`` matches (code fences ignored), in pattern order."""
    text = _FENCE.sub(" ", message)
    return tuple(name for name, pattern in REQUEST_PATTERNS if pattern.search(text))


def fingerprint_script(workspace: str = APP_SOURCE) -> str:
    """Bash printing one digest of the workspace's files (the snapshot's excludes pruned)."""
    prune = " -o ".join(f"-name {shlex.quote(name)}" for name in SNAPSHOT_EXCLUDES)
    return (
        f"cd {shlex.quote(workspace)} 2>/dev/null || {{ echo missing; exit 0; }}; "
        f"find . \\( {prune} \\) -prune -o \\( -type f -o -type l \\) -print0 2>/dev/null "
        "| LC_ALL=C sort -z | xargs -0r sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1"
    )


def continuation_prompt(task: str, message: str) -> str:
    excerpt = message.strip()[:MESSAGE_EXCERPT_CHARS] or "(no message)"
    tokens = {"TASK": task, "MESSAGE": excerpt, "ASSUMPTIONS": ASSUMPTIONS_FILE}
    prompt = render(CONTINUE_PROMPT, tokens)
    if len(prompt.encode()) <= PROMPT_ARG_BUDGET:
        return prompt
    return render(CONTINUE_PROMPT, {**tokens, "MESSAGE": excerpt[:200]})


class ContinueOnStallOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    component: str = Field(default=COMPONENT)
    workspace: str = Field(default=APP_SOURCE, pattern=r"^/[A-Za-z0-9._/-]+$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class _Run:
    """One native session the wrapper ran."""

    role: str  # "session" or "continuation"
    logs: str  # relative to the wrapper's logs dir
    context: AgentContext = field(default_factory=AgentContext)
    error: str | None = None
    started_at: str | None = None
    duration_sec: float | None = None

    def record(self) -> dict[str, Any]:
        usage = {key: getattr(self.context, key) for key in USAGE_FIELDS}
        return {
            "role": self.role,
            "logs": self.logs,
            "error": self.error,
            "started_at": self.started_at,
            "duration_sec": self.duration_sec,
            **usage,
        }


@dataclass
class _Observation:
    before: str
    after: str | None = None
    message: str = ""
    message_source: str | None = None
    matched: tuple[str, ...] = ()

    @property
    def changed(self) -> bool | None:
        return None if self.after is None else self.after != self.before


@dataclass
class _State:
    runs: list[_Run] = field(default_factory=list)
    first: _Observation | None = None
    continuation: _Observation | None = None
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def stalled(self) -> bool:
        first = self.first
        return first is not None and first.changed is False and bool(first.matched)


class ContinueOnStallAgent(BaseAgent):
    """Runs each session of the wrapped agent; continues once a session that stalled."""

    options_model = ContinueOnStallOptions

    @staticmethod
    def name() -> str:
        return "raidar-continue-on-stall"

    def version(self) -> str | None:
        return VERSION

    @property
    def stall_options(self) -> ContinueOnStallOptions:
        return cast(ContinueOnStallOptions, self.options)

    def _native(self, logs_dir: Path) -> BaseAgent:
        return create_inner(
            self, self.stall_options.inner_agent, logs_dir, model_name=self.model_name
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._native(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._state = state = _State()
        task = render_task(self.stall_options.inner_agent, instruction)
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

    async def _run_native(
        self, environment: BaseEnvironment, state: _State, role: str, prompt: str
    ) -> None:
        run = _Run(role=role, logs=".", started_at=_now())
        state.runs.append(run)
        native = self._native(self.logs_dir)
        started = time.monotonic()
        try:
            await native.run(prompt, environment, run.context)
        except BaseException as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            run.duration_sec = round(time.monotonic() - started, 3)

    async def _fingerprint(self, environment: BaseEnvironment) -> str:
        result = await environment.exec(fingerprint_script(self.stall_options.workspace))
        return (result.stdout or "").strip() or f"unreadable (exit {result.return_code})"

    async def _names(self, environment: BaseEnvironment) -> set[str]:
        logs = shlex.quote(str(self.environment_logs_dir))
        result = await environment.exec(f"ls -A {logs} 2>/dev/null || true")
        return {line for line in (result.stdout or "").splitlines() if line.strip()}

    async def _observe(self, environment: BaseEnvironment, seen: _Observation) -> None:
        seen.after = await self._fingerprint(environment)
        seen.message_source, seen.message = await self._final_message(environment)
        seen.matched = detect_request(seen.message)

    async def _final_message(self, environment: BaseEnvironment) -> tuple[str | None, str]:
        """The session's last assistant message, from the harness's stream in the logs dir."""
        logs = shlex.quote(str(self.environment_logs_dir))
        names = " ".join(MESSAGE_FILES)
        result = await environment.exec(
            f"for f in {names}; do if [ -f {logs}/$f ]; then echo $f; "
            f"tail -c {MESSAGE_TAIL_BYTES} {logs}/$f; break; fi; done"
        )
        source, _, content = (result.stdout or "").partition("\n")
        if source not in MESSAGE_FILES:
            return None, ""
        with tempfile.TemporaryDirectory(prefix="raidar-stall-") as tmp:
            (Path(tmp) / source).write_text(content, encoding="utf-8")
            return source, final_message(Path(tmp))

    async def _file_stalled_logs(self, environment: BaseEnvironment, before: set[str]) -> None:
        """Move what the stalled session wrote to the logs dir under the stall dir."""
        new = sorted(await self._names(environment) - before - {STALL_DIR})
        target = PurePosixPath(str(self.environment_logs_dir)) / STALL_DIR / STALLED_SESSION_DIR
        moves = " ".join(shlex.quote(name) for name in new)
        command = f"mkdir -p {shlex.quote(str(target))}"
        if new:
            command = f"cd {shlex.quote(str(self.environment_logs_dir))} && {command} && " + (
                f"mv -- {moves} {shlex.quote(str(target))}/"
            )
        result = await environment.exec(command)
        if result.return_code != 0:
            raise RuntimeError(
                f"continue-on-stall could not file the stalled session: {result.stderr}"
            )

    def _record(self, state: _State) -> dict[str, Any]:
        def seen(item: _Observation | None) -> dict[str, Any] | None:
            if item is None:
                return None
            return {
                "fingerprint_before": item.before,
                "fingerprint_after": item.after,
                "changed": item.changed,
                "final_message": item.message[:MESSAGE_EXCERPT_CHARS],
                "final_message_source": item.message_source,
                "matched": list(item.matched),
            }

        return {
            "component": self.stall_options.component,
            "workspace": self.stall_options.workspace,
            "session": seen(state.first),
            "stalled": state.stalled,
            "continued": state.continuation is not None,
            "continuation": seen(state.continuation),
            "runs": [run.record() for run in state.runs],
        }

    async def _write_record(self, environment: BaseEnvironment, record: dict[str, Any]) -> None:
        folder = PurePosixPath(str(self.environment_logs_dir)) / STALL_DIR
        data = base64.b64encode(json.dumps(record, indent=2, sort_keys=True).encode()).decode()
        await environment.exec(
            f"mkdir -p {shlex.quote(str(folder))} && printf %s {data} | base64 -d > "
            f"{shlex.quote(str(folder / STALL_RECORD))}"
        )

    def _sum_into(self, context: AgentContext) -> None:
        state = self._state
        for key, value in sum_usage([run.context for run in state.runs]).items():
            setattr(context, key, value)
        state.record["runs"] = [run.record() for run in state.runs]
        context.metadata = {**(context.metadata or {}), "continue_on_stall": state.record}

    def _run_dir(self, run: _Run) -> Path:
        return self.logs_dir if run.logs == "." else self.logs_dir / STALL_DIR / run.logs

    def populate_context_post_run(self, context: AgentContext) -> None:
        state = getattr(self, "_state", None)
        if state is None:
            return
        for run in state.runs:
            if run.context.is_empty():
                self._native(self._run_dir(run)).populate_context_post_run(run.context)
        self._sum_into(context)
        record = self.logs_dir / STALL_DIR / STALL_RECORD
        if record.is_file():  # the session's usage, now that it is known
            record.write_text(json.dumps(state.record, indent=2, sort_keys=True))
        if state.continuation is not None:
            self._write_root_trajectory(state)

    def _write_root_trajectory(self, state: _State) -> None:
        subagents: list[Trajectory] = []
        steps: list[dict[str, Any]] = []
        for index, run in enumerate(state.runs, start=1):
            directory = self._run_dir(run)
            name = f"{run.role}_{index}"
            ref: dict[str, Any] = {"trajectory_id": name}
            path = directory / "trajectory.json"
            if path.is_file():
                sub = Trajectory.model_validate_json(path.read_text())
                subagents.append(sub.model_copy(update={"trajectory_id": name}))
            else:
                ref = {"trajectory_path": run.logs, "extra": {"missing": True}}
            steps.append(
                {
                    "step_id": index,
                    "source": "system",
                    "message": f"continue-on-stall {run.role}",
                    "observation": {
                        "results": [{"content": run.error, "subagent_trajectory_ref": [ref]}]
                    },
                }
            )
        usage = sum_usage([run.context for run in state.runs])
        trajectory = Trajectory.model_validate(
            {
                "session_id": self.session_id,
                "agent": {"name": self.name(), "version": VERSION, "model_name": self.model_name},
                "steps": steps,
                "final_metrics": {
                    "total_prompt_tokens": usage["n_input_tokens"],
                    "total_completion_tokens": usage["n_output_tokens"],
                    "total_cached_tokens": usage["n_cache_tokens"],
                    "total_cost_usd": usage["cost_usd"],
                    "total_steps": len(state.runs),
                },
                "subagent_trajectories": subagents or None,
            }
        )
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), indent=2) + "\n"
        )
