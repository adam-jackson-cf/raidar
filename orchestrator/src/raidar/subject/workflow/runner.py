"""The workflow runner: the Harbor agent of every real arm (design section 5.5).

Harbor runs one agent phase per checkpoint (one ``run`` per step), so the reaper, the liveness
monitor and the per-step reset see the same phases they saw for the native agent. Inside the
phase the runner drives the native agent (``inner_agent``, built by ``create_inner``) through
the workflow's enabled stages (``raidar.subject.workflow.stage``), host-side, one *turn* at a
time, all in the checkpoint's one primary session:

1. every enabled stage prepares (before the agent starts);
2. one prompt (``compose``: build's frame, each stage's fragment at its token, the task text at
   ``%%%TASK%%%``) runs as the first turn, in a fresh session (``run``);
3. when the agent stops, each enabled stage's checks run in stage order; the first that returns
   a message resumes the same session with it (``resume``, a turn of that stage) and the checks
   run again. Each stage bounds its own resumes; when none asks, the checkpoint ends;
4. every stage closes, also when a turn failed (``turn_error``) or the phase was cancelled
   (``deadline``), and its record goes into ``workflow.json`` under its name.

Each checkpoint starts a fresh session because Harbor empties ``/logs/agent`` (the session
store) before every step.

Per turn, under the step's agent logs at ``workflow/<stage>-<n>/``:

- ``prompt.txt``: exactly what the agent was sent;
- a copy of everything the native agent left in ``/logs/agent`` (``sessions/``, its output
  stream). The active session store ``/logs/agent/sessions`` is copied, never moved, so a later
  turn can resume it;
- ``trajectory.json``: the native agent's trajectory, built from that copy.

``workflow/workflow.json`` records the enabled ``stages``, the step records holding text a
stage placed in the workspace's instruction files (``instructions``), and every turn: stage,
turn index, session id, whether it resumed, start, end, seconds, error, the session's
cumulative token total after the turn and
the turn's own tokens, and, from a native agent that isolates itself
(``raidar.subject.agent.isolated_codex.IsolatedCodex``), ``harness_isolation`` (the flags and
settings it applied to the turn's command) and ``agent_process`` (the user, ``PATH`` and
``TMPDIR`` the agent process ran with). A native context after a resumed turn is the
session's cumulative total, so a turn's tokens are the delta from the previous turn of the
same session; the phase's usage is the sum of the deltas, which reconciles with the final
session totals by construction. When a turn's usage cannot be read the phase reports none
rather than a partial sum. The last turn's trajectory becomes the step's ``trajectory.json``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.subject.agent.isolated_codex import IsolatedCodex
from raidar.subject.workflow.components import skill_files
from raidar.subject.workflow.definition import StageType, WorkflowDefinition
from raidar.subject.workflow.factory import workflow_stages
from raidar.subject.workflow.inner import InnerAgentSpec, create_inner
from raidar.subject.workflow.stage import DEADLINE, TURN_ERROR, Checkpoint, Stage, compose

RUNNER_NAME = "raidar-workflow"
WORKFLOW_DIR = "workflow"
RECORD_FILE = "workflow.json"
RECORD_KIND = "raidar-workflow"
PROMPT_FILE = "prompt.txt"
TRAJECTORY_FILE = "trajectory.json"
STEP_RECORD_PREFIX = "raidar-"
"""Step-level lab records in ``/logs/agent`` (reset report, provision records): not a turn's."""


class WorkflowRunnerOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    workflow: str
    definition: WorkflowDefinition
    skills_placement: str = ".agents/skills"


@dataclass(frozen=True)
class Tokens:
    """A session's token counts (cumulative) or one turn's share of them (a delta)."""

    input: int
    cached: int
    output: int
    cost_usd: float | None

    @classmethod
    def of(cls, context: AgentContext) -> Tokens | None:
        counts = (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens)
        if any(count is None for count in counts):
            return None
        n_input, n_cached, n_output = cast(tuple[int, int, int], counts)
        return cls(n_input, n_cached, n_output, context.cost_usd)

    def minus(self, earlier: Tokens) -> Tokens:
        cost = (
            None
            if self.cost_usd is None or earlier.cost_usd is None
            else round(self.cost_usd - earlier.cost_usd, 10)
        )
        return Tokens(
            self.input - earlier.input,
            self.cached - earlier.cached,
            self.output - earlier.output,
            cost,
        )

    def plus(self, other: Tokens) -> Tokens:
        cost = (
            None
            if self.cost_usd is None or other.cost_usd is None
            else self.cost_usd + other.cost_usd
        )
        return Tokens(
            self.input + other.input, self.cached + other.cached, self.output + other.output, cost
        )

    def record(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "cached": self.cached,
            "output": self.output,
            "cost_usd": self.cost_usd,
        }


ZERO = Tokens(0, 0, 0, 0.0)


@dataclass
class Turn:
    stage: StageType
    index: int
    resumed: bool
    prompt_sha256: str
    started_at: str
    started: float
    finished_at: str | None = None
    seconds: float | None = None
    error: str | None = None
    settle_error: str | None = None
    session_id: str | None = None
    session_total: Tokens | None = None
    tokens: Tokens | None = None
    harness_isolation: dict[str, Any] | None = None
    agent_process: dict[str, str | None] | None = None

    @property
    def log_dir(self) -> str:
        return f"{WORKFLOW_DIR}/{self.stage}-{self.index}"

    def stop(self, error: str | None = None) -> None:
        self.finished_at = _now()
        self.seconds = round(time.monotonic() - self.started, 3)
        self.error = error

    def record(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "turn": self.index,
            "log_dir": self.log_dir,
            "session_id": self.session_id,
            "resumed": self.resumed,
            "prompt_sha256": self.prompt_sha256,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": self.seconds,
            "error": self.error,
            "settle_error": self.settle_error,
            "session_total": self.session_total.record() if self.session_total else None,
            "tokens": self.tokens.record() if self.tokens else None,
            "harness_isolation": self.harness_isolation,
            "agent_process": self.agent_process,
        }


@dataclass
class _Run:
    turns: list[Turn] = field(default_factory=list)

    def delta(self, turn: Turn) -> Tokens | None:
        """The turn's tokens: its session total minus the same session's previous total."""
        if turn.session_total is None:
            return None
        earlier = [
            item.session_total
            for item in self.turns
            if item is not turn and item.session_id == turn.session_id and item.session_total
        ]
        return turn.session_total.minus(earlier[-1]) if earlier else turn.session_total

    def usage(self) -> Tokens | None:
        """The phase's tokens (sum of turn deltas); ``None`` when any turn's is unknown."""
        total = ZERO
        for turn in self.turns:
            if turn.tokens is None:
                return None
            total = total.plus(turn.tokens)
        return total if self.turns else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _session_id(turn_dir: Path) -> str | None:
    path = turn_dir / TRAJECTORY_FILE
    try:
        value = json.loads(path.read_text()).get("session_id")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


class WorkflowRunner(BaseAgent):
    """Runs the arm's workflow on its native agent, one turn at a time (module docstring)."""

    options_model = WorkflowRunnerOptions

    @staticmethod
    def name() -> str:
        return RUNNER_NAME

    @property
    def runner_options(self) -> WorkflowRunnerOptions:
        return cast(WorkflowRunnerOptions, self.options)

    def version(self) -> str | None:
        """The native agent's pinned CLI version (what the turns run)."""
        pinned = self.runner_options.inner_agent.kwargs.get("version")
        return str(pinned) if pinned is not None else None

    def _inner(self, logs_dir: Path) -> BaseAgent:
        return create_inner(
            self, self.runner_options.inner_agent, logs_dir, model_name=self.model_name
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = _Run()
        stages = workflow_stages(self.runner_options.definition)
        self.components: list[dict[str, str]] = []
        checkpoint = Checkpoint(environment, self.logs_dir, instruction)
        cut: str | None = None
        try:
            await self._work(checkpoint, state, stages, instruction)
        except asyncio.CancelledError:
            cut = DEADLINE
            raise
        except Exception:
            cut = TURN_ERROR
            raise
        finally:
            await asyncio.shield(self._close(checkpoint, stages, cut))
            self._finish(context, state, stages)

    async def _work(
        self, checkpoint: Checkpoint, state: _Run, stages: list[Stage], instruction: str
    ) -> None:
        """Prepare every stage, run the one prompt, then resume while a stage's checks ask."""
        await self._place_components(checkpoint)
        for stage in stages:
            await stage.prepare(checkpoint)
        prompt = compose(stages, instruction)
        await self._turn(checkpoint.environment, state, "build", prompt, resume=False)
        while (followup := await self._checks(checkpoint, stages, state.turns[-1])) is not None:
            stage_type, message = followup
            await self._turn(checkpoint.environment, state, stage_type, message, resume=True)

    async def _place_components(self, checkpoint: Checkpoint) -> None:
        files = skill_files(self.runner_options.definition, self.runner_options.skills_placement)
        for relative, text in files.items():
            record = f"{WORKFLOW_DIR}/components/{relative}"
            path = checkpoint.logs_dir / record
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            await checkpoint.environment.upload_file(path, f"/app/{relative}")
            self.components.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "record": record,
                }
            )

    @staticmethod
    async def _checks(
        checkpoint: Checkpoint, stages: list[Stage], turn: Turn
    ) -> tuple[StageType, str] | None:
        """The first stage, in stage order, whose checks resume the session, and its message."""
        for stage in stages:
            message = await stage.check(checkpoint, turn.log_dir)
            if message is not None:
                return stage.name, message
        return None

    @staticmethod
    async def _close(checkpoint: Checkpoint, stages: list[Stage], cut: str | None) -> None:
        for stage in stages:
            await stage.close(checkpoint, cut)

    async def _turn(
        self,
        environment: BaseEnvironment,
        state: _Run,
        stage: StageType,
        prompt: str,
        *,
        resume: bool,
    ) -> None:
        turn = Turn(
            stage=stage,
            index=len(state.turns) + 1,
            resumed=resume,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            started_at=_now(),
            started=time.monotonic(),
        )
        state.turns.append(turn)
        turn_dir = self.logs_dir / turn.log_dir
        turn_dir.mkdir(parents=True, exist_ok=True)
        (turn_dir / PROMPT_FILE).write_text(prompt, encoding="utf-8")
        inner = self._inner(turn_dir)
        context = AgentContext()
        send = inner.resume if resume else inner.run
        try:
            await send(prompt, environment, context)
        except asyncio.CancelledError:
            turn.stop("CancelledError: the agent phase was cancelled (agent timeout)")
            await asyncio.shield(self._settle(environment, state, turn, inner, context))
            raise
        except Exception as exc:
            turn.stop(f"{type(exc).__name__}: {exc}")
            await self._settle(environment, state, turn, inner, context)
            raise
        turn.stop()
        await self._settle(environment, state, turn, inner, context)

    async def _settle(
        self,
        environment: BaseEnvironment,
        state: _Run,
        turn: Turn,
        inner: BaseAgent,
        context: AgentContext,
    ) -> None:
        """Copy the turn's logs into its directory and read its isolation, session and usage."""
        if isinstance(inner, IsolatedCodex):
            turn.harness_isolation = inner.harness_isolation
            turn.agent_process = inner.agent_process
        turn_dir = self.logs_dir / turn.log_dir
        try:
            await self._copy_turn_logs(environment, turn.log_dir, turn_dir)
        except Exception as exc:  # noqa: BLE001 - recorded on the turn; usage stays unknown
            turn.settle_error = f"{type(exc).__name__}: {exc}"
            return
        if context.is_empty():
            inner.populate_context_post_run(context)
        turn.session_id = _session_id(turn_dir)
        turn.session_total = Tokens.of(context)
        turn.tokens = state.delta(turn)

    async def _copy_turn_logs(
        self, environment: BaseEnvironment, relative: str, turn_dir: Path
    ) -> None:
        logs = PurePosixPath(str(self.environment_logs_dir))
        command = (
            f"cd {shlex.quote(str(logs))} && mkdir -p {shlex.quote(relative)} && "
            f"find . -mindepth 1 -maxdepth 1 ! -name {WORKFLOW_DIR} "
            f"! -name '{STEP_RECORD_PREFIX}*' -exec cp -R {{}} {shlex.quote(relative)}/ \\;"
        )
        result = await environment.exec(command)
        if result.return_code != 0:
            raise RuntimeError(f"could not copy the turn's logs: {result.stderr or result.stdout}")
        if not environment.capabilities.mounted:
            await environment.download_dir(str(logs / relative), turn_dir)

    def _record(self, state: _Run, stages: list[Stage], usage: Tokens | None) -> dict[str, Any]:
        """``workflow.json``: the stages, the turns, the phase's tokens and each stage's record."""
        record: dict[str, Any] = {
            "kind": RECORD_KIND,
            "workflow": self.runner_options.workflow,
            "stages": [stage.name for stage in stages],
            "instructions": [path for stage in stages for path in stage.instructions],
            "turns": [turn.record() for turn in state.turns],
            "tokens": usage.record() if usage else None,
            "components": self.components,
        }
        outputs = {stage.name: stage.record() for stage in stages}
        return {**record, **{name: value for name, value in outputs.items() if value is not None}}

    def _finish(self, context: AgentContext, state: _Run, stages: list[Stage]) -> None:
        usage = state.usage()
        if usage is not None:
            context.n_input_tokens = usage.input
            context.n_cache_tokens = usage.cached
            context.n_output_tokens = usage.output
            context.cost_usd = usage.cost_usd
        record = self._record(state, stages, usage)
        context.metadata = {**(context.metadata or {}), "workflow": record}
        workflow_dir = self.logs_dir / WORKFLOW_DIR
        workflow_dir.mkdir(parents=True, exist_ok=True)
        (workflow_dir / RECORD_FILE).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        last = state.turns[-1] if state.turns else None
        trajectory = self.logs_dir / last.log_dir / TRAJECTORY_FILE if last else None
        if trajectory is not None and trajectory.is_file():
            (self.logs_dir / TRAJECTORY_FILE).write_bytes(trajectory.read_bytes())
