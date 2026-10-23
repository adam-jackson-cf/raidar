"""``transient-retry@1`` session wrapper: rerun a session the provider dropped, at most twice.

A session that fails because the provider was briefly unavailable (Harbor's
``ApiOverloadedError``, ``ApiConnectionClosedError``, ``ApiResponseStalledError``,
``ApiInternalServerError`` or ``ApiRateLimitError``: classified by Harbor from the
harness's own output, e.g. Codex's "Selected model is at capacity") fails its
step as an infrastructure error, and the trial is invalid. In ``smoke-03`` of
the guardrails experiment 3 of 16 r2 steps failed that way, and ``smoke-03-r2``
1 of 8, so no r2 trial was valid.

This wrapper runs each session of the native agent and, when it raises one of
those errors, waits (``WAITS_SEC``: 60 s, then 180 s) and starts the session
again, at most ``MAX_RETRIES`` (2) times: the same task, after a fixed preface
saying the earlier session was cut off by the provider and the workspace keeps
whatever it changed. Any other error, or the last retry's error, is raised
unchanged, so Harbor and the step's validity checks classify it exactly as the
unwrapped agent's. The wait counts against the step's agent timeout.

Recorded for every session, retried or not:
``/logs/agent/transient-retry/transient-retry.json`` (each attempt's error
class and message head, duration and the wait before it, ``retries`` and
``recovered``). A failed attempt's logs are moved to
``transient-retry/attempt_<n>`` before the next one; every attempt's tokens and
cost are summed into the session's context (unknown if any attempt did not
report, never a partial sum), and after a retry the session's
``trajectory.json`` is a root trajectory embedding each attempt's. It is
harness-agnostic and names nothing problem-specific.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import (
    ApiConnectionClosedError,
    ApiInternalServerError,
    ApiOverloadedError,
    ApiRateLimitError,
    ApiResponseStalledError,
)
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from pydantic import Field

from raidar.components.runtime.continue_on_stall import _now, _Run
from raidar.components.runtime.review_loop import PROMPT_ARG_BUDGET, render, sum_usage
from raidar.components.runtime.session_chain import InnerAgentSpec, create_inner, render_task

COMPONENT = "transient-retry@1"
VERSION = "1"
RETRY_DIR = "transient-retry"
RETRY_RECORD = "transient-retry.json"
ATTEMPT_DIR = "attempt_{index}"
MAX_RETRIES = 2
WAITS_SEC = (60.0, 180.0)
ERROR_HEAD_CHARS = 300
RETRYABLE: tuple[type[BaseException], ...] = (
    ApiOverloadedError,
    ApiConnectionClosedError,
    ApiResponseStalledError,
    ApiInternalServerError,
    ApiRateLimitError,
)

RETRY_PREFACE = """\
Your previous session on this task was cut off by the model provider (a temporary
service error, not something you did). The workspace keeps every change that session
made. Continue the task below from the workspace's current state.

%%%TASK%%%
"""


def retry_prompt(task: str) -> str:
    """The task after the retry preface (the bare task if the preface would not fit)."""
    prompt = render(RETRY_PREFACE, {"TASK": task})
    return prompt if len(prompt.encode()) <= PROMPT_ARG_BUDGET else task


def is_retryable(exc: BaseException) -> bool:
    """A provider error Harbor classified as transient."""
    return isinstance(exc, RETRYABLE)


class TransientRetryOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    component: str = Field(default=COMPONENT)
    max_retries: int = Field(default=MAX_RETRIES, ge=0, le=len(WAITS_SEC))


@dataclass
class _Attempt:
    run: _Run
    waited_sec: float = 0.0
    error_class: str | None = None

    def record(self) -> dict[str, Any]:
        return {**self.run.record(), "waited_sec": self.waited_sec, "error_class": self.error_class}


@dataclass
class _RetryState:
    attempts: list[_Attempt] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def retries(self) -> int:
        return max(len(self.attempts) - 1, 0)


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _attempt_from(item: dict[str, Any]) -> _Attempt:
    """An attempt as its record describes it (usage is read again from its logs)."""
    run = _Run(
        role="attempt",
        logs=str(item.get("logs") or "."),
        error=item.get("error") if isinstance(item.get("error"), str) else None,
        started_at=item.get("started_at") if isinstance(item.get("started_at"), str) else None,
        duration_sec=_optional_float(item.get("duration_sec")),
    )
    error_class = item.get("error_class")
    return _Attempt(
        run,
        _optional_float(item.get("waited_sec")) or 0.0,
        error_class if isinstance(error_class, str) else None,
    )


class TransientRetryAgent(BaseAgent):
    """Runs each session of the wrapped agent; retries it after a transient provider error."""

    options_model = TransientRetryOptions

    @staticmethod
    def name() -> str:
        return "raidar-transient-retry"

    def version(self) -> str | None:
        return VERSION

    @property
    def retry_options(self) -> TransientRetryOptions:
        return cast(TransientRetryOptions, self.options)

    def _native(self, logs_dir: Path) -> BaseAgent:
        return create_inner(
            self, self.retry_options.inner_agent, logs_dir, model_name=self.model_name
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._native(self.logs_dir).setup(environment)

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._state = state = _RetryState()
        task = render_task(self.retry_options.inner_agent, instruction)
        try:
            await self._attempts(environment, state, task)
        finally:
            state.record = self._record(state)
            await self._write_record(environment, state.record)
            if all(not item.run.context.is_empty() for item in state.attempts):
                self._sum_into(context)

    async def _attempts(self, environment: BaseEnvironment, state: _RetryState, task: str) -> None:
        waited = 0.0
        while True:
            before = await self._names(environment)
            prompt = task if not state.attempts else retry_prompt(task)
            attempt = _Attempt(_Run(role="attempt", logs=".", started_at=_now()), waited)
            state.attempts.append(attempt)
            try:
                await self._run_native(environment, attempt, prompt)
                return
            except Exception as exc:
                attempt.error_class = type(exc).__name__
                if not is_retryable(exc) or state.retries >= self.retry_options.max_retries:
                    raise
            index = len(state.attempts)
            await self._file_attempt_logs(environment, before, index)
            attempt.run.logs = ATTEMPT_DIR.format(index=index)
            waited = WAITS_SEC[index - 1]
            await self._sleep(waited)

    async def _run_native(
        self, environment: BaseEnvironment, attempt: _Attempt, prompt: str
    ) -> None:
        run = attempt.run
        started = time.monotonic()
        try:
            await self._native(self.logs_dir).run(prompt, environment, run.context)
        except BaseException as exc:
            run.error = f"{type(exc).__name__}: {exc}"[:ERROR_HEAD_CHARS]
            raise
        finally:
            run.duration_sec = round(time.monotonic() - started, 3)

    async def _names(self, environment: BaseEnvironment) -> set[str]:
        logs = shlex.quote(str(self.environment_logs_dir))
        result = await environment.exec(f"ls -A {logs} 2>/dev/null || true")
        return {line for line in (result.stdout or "").splitlines() if line.strip()}

    async def _file_attempt_logs(
        self, environment: BaseEnvironment, before: set[str], index: int
    ) -> None:
        """Move what the failed attempt wrote to the logs dir under the retry dir."""
        new = sorted(await self._names(environment) - before - {RETRY_DIR})
        target = (
            PurePosixPath(str(self.environment_logs_dir))
            / RETRY_DIR
            / ATTEMPT_DIR.format(index=index)
        )
        command = f"mkdir -p {shlex.quote(str(target))}"
        if new:
            moves = " ".join(shlex.quote(name) for name in new)
            command = f"cd {shlex.quote(str(self.environment_logs_dir))} && {command} && " + (
                f"mv -- {moves} {shlex.quote(str(target))}/"
            )
        result = await environment.exec(command)
        if result.return_code != 0:
            raise RuntimeError(
                f"transient-retry could not file the failed attempt: {result.stderr}"
            )

    def _record(self, state: _RetryState) -> dict[str, Any]:
        last = state.attempts[-1] if state.attempts else None
        return {
            "component": self.retry_options.component,
            "max_retries": self.retry_options.max_retries,
            "retries": state.retries,
            "recovered": state.retries > 0 and last is not None and last.run.error is None,
            "attempts": [item.record() for item in state.attempts],
        }

    async def _write_record(self, environment: BaseEnvironment, record: dict[str, Any]) -> None:
        folder = PurePosixPath(str(self.environment_logs_dir)) / RETRY_DIR
        data = base64.b64encode(json.dumps(record, indent=2, sort_keys=True).encode()).decode()
        await environment.exec(
            f"mkdir -p {shlex.quote(str(folder))} && printf %s {data} | base64 -d > "
            f"{shlex.quote(str(folder / RETRY_RECORD))}"
        )

    def _sum_into(self, context: AgentContext) -> None:
        state = self._state
        for key, value in sum_usage([item.run.context for item in state.attempts]).items():
            setattr(context, key, value)
        state.record["attempts"] = [item.record() for item in state.attempts]
        context.metadata = {**(context.metadata or {}), "transient_retry": state.record}

    def _run_dir(self, run: _Run) -> Path:
        return self.logs_dir if run.logs == "." else self.logs_dir / RETRY_DIR / run.logs

    def _recorded_state(self) -> _RetryState | None:
        """The state of a session this instance did not run, from its record in the logs dir.

        An outer wrapper may build a fresh instance on a session's logs dir only
        to read its usage (``continue-on-stall`` does); the record names each
        attempt's logs.
        """
        path = self.logs_dir / RETRY_DIR / RETRY_RECORD
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        attempts = record.get("attempts") if isinstance(record, dict) else None
        if not isinstance(attempts, list) or not attempts:
            return None
        state = _RetryState(record=record)
        for item in attempts:
            state.attempts.append(_attempt_from(item if isinstance(item, dict) else {}))
        return state

    def populate_context_post_run(self, context: AgentContext) -> None:
        state = getattr(self, "_state", None) or self._recorded_state()
        if state is None:
            self._native(self.logs_dir).populate_context_post_run(context)
            return
        self._state = state
        for item in state.attempts:
            if item.run.context.is_empty():
                self._native(self._run_dir(item.run)).populate_context_post_run(item.run.context)
        self._sum_into(context)
        record = self.logs_dir / RETRY_DIR / RETRY_RECORD
        if record.is_file():  # the session's usage, now that it is known
            record.write_text(json.dumps(state.record, indent=2, sort_keys=True))
        if state.retries:
            self._write_root_trajectory(state)

    def _write_root_trajectory(self, state: _RetryState) -> None:
        subagents: list[Trajectory] = []
        steps: list[dict[str, Any]] = []
        for index, item in enumerate(state.attempts, start=1):
            run = item.run
            name = f"attempt_{index}"
            ref: dict[str, Any] = {"trajectory_id": name}
            path = self._run_dir(run) / "trajectory.json"
            if path.is_file():
                sub = Trajectory.model_validate_json(path.read_text())
                subagents.append(sub.model_copy(update={"trajectory_id": name}))
            else:
                ref = {"trajectory_path": run.logs, "extra": {"missing": True}}
            steps.append(
                {
                    "step_id": index,
                    "source": "system",
                    "message": f"transient-retry attempt {index}",
                    "observation": {
                        "results": [{"content": run.error, "subagent_trajectory_ref": [ref]}]
                    },
                }
            )
        usage = sum_usage([item.run.context for item in state.attempts])
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
                    "total_steps": len(state.attempts),
                },
                "subagent_trajectories": subagents or None,
            }
        )
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), indent=2) + "\n"
        )
