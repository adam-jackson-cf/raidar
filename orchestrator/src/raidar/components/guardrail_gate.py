"""``guardrail-gate@1`` workflow agent: the repository refuses work its commit gate fails.

The gate replicates a real repository refusing a commit, the same way for every
harness (no harness hooks). It wraps the arm's agent like review-loop@2 does,
but its checker is deterministic, not a reviewer. One step runs:

    work session -> gate -> [fix session -> gate]* (at most ``max_rounds`` fixes)

- **work session**: the arm's agent, given the step instruction exactly as the
  unwrapped agent would get it (a ``prompt-wrapper@1`` template applied);
- **gate**: inside the trial container, as the agent user, in the workspace, the
  single command the workspace's guardrails pack names as its commit gate
  (``command``; this component never decides which checks run). In ``commit``
  mode (the default) the loop stages everything (``git add -A``) and runs
  ``git commit`` with that command installed as the pre-commit hook, so a failing
  hook literally refuses the commit and a passing one records it. A workspace
  that is not yet its own git repository is initialised first (``.git`` is outside
  the graded snapshot). In ``command`` mode the command runs on its own;
- **fix session**: a fresh session of the same agent, given the task and the
  gate's exact output (clipped only to fit the harness argument limit). The
  step ends when the gate passes, when ``max_rounds`` fix sessions have run, or
  when the gate cannot run at all (``gate_error``: no fix session, since the
  agent cannot act on an infrastructure failure).

The gate runs only during the agent phase, so the hidden verifier's tests are
never in the container (F4); the component also refuses a command that names
verifier paths. The agent's own tests may run if the pack's gate runs them.

Recorded in ``/logs/agent/guardrail-gate/guardrail-gate.json`` and in the
step's ``AgentContext.metadata["guardrail_gate"]``: every gate run (exit codes,
checks failed and passed when the output's format is recognised, the output's
sha256 and size, the full output log, its duration and its report dir), every
session's usage, start, end and duration, the fix rounds used, the termination
reason, and the deterministic manipulation-check signal
``gate_clean_at_step_end`` (``True``/``False``; ``None`` when no gate run
completed). Every session's tokens and cost are summed into the step's context,
so the arm pays for its fix sessions, and every session's trajectory is embedded
in the step's root ``trajectory.json`` (as review-loop does).

Each gate run exports ``RAIDAR_GATE_RUN`` (its number) and
``RAIDAR_GATE_REPORT_DIR`` (``/logs/agent/guardrail-gate/gate_run_<n>``, created
empty) to the command, so a pack's gate can write a machine-readable report per
run (``guardrail-check --json-dir "$RAIDAR_GATE_REPORT_DIR"``); ``gate-effort@1``
reads it beside the run's output.

Recognised check formats (``parse_checks``): pre-commit's
``<name>....Passed|Failed|Skipped`` lines, explicit ``gate-check <name> pass|fail``
lines a pack's gate script may print, and make's ``make: *** [<target>] Error N``.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from harbor.models.trial.config import AgentConfig
from harbor.utils.templating import render_prompt_template

from raidar.components.gate_run import (
    GATE_DIR_NAME,
    GATE_RECORD,
    PROMPT_TEMPLATE_KWARG,
    SIGNAL,
    VERSION,
    GateRun,
    GuardrailGateOptions,
    fix_prompt,
    gate_script,
    read_gate_run,
    report_dir_for,
)
from raidar.components.review_loop import STEP_LEVEL_LOGS, USAGE_FIELDS, sum_usage

# ---- the wrapper agent -----------------------------------------------------------------


@dataclass
class _Session:
    index: int
    role: str  # "work" or "fix"
    round: int
    context: AgentContext
    directory: Path
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_sec: float | None = None

    @property
    def name(self) -> str:
        return f"stage_{self.index}_{self.role}"

    def record(self) -> dict[str, Any]:
        usage = {key: getattr(self.context, key) for key in USAGE_FIELDS}
        return {
            "index": self.index,
            "stage": self.role,
            "round": self.round,
            "stage_dir": f"{GATE_DIR_NAME}/{self.name}",
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": self.duration_sec,
            **usage,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class _GateState:
    sessions: list[_Session] = field(default_factory=list)
    runs: list[GateRun] = field(default_factory=list)
    status: str = "failed"
    termination_reason: str = "loop_exception"
    error: str | None = None
    failure: BaseException | None = None

    @property
    def clean_at_end(self) -> bool | None:
        """The last gate run's verdict, if it decided and no session ran after it."""
        if not self.runs or len(self.sessions) > len(self.runs) or self.runs[-1].error:
            return None
        return self.runs[-1].passed


class GuardrailGateAgent(BaseAgent):
    """Wraps a Harbor agent: work, then fix sessions until the commit gate passes."""

    options_model = GuardrailGateOptions

    @staticmethod
    def name() -> str:
        return "raidar-guardrail-gate"

    def version(self) -> str | None:
        return VERSION

    @property
    def gate_options(self) -> GuardrailGateOptions:
        return cast(GuardrailGateOptions, self.options)

    def _inner(self, logs_dir: Path) -> BaseAgent:
        """The wrapped agent without its prompt template: the loop applies that itself."""
        inner = self.gate_options.inner_agent
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

    def _task(self, instruction: str) -> str:
        value = self.gate_options.inner_agent.kwargs.get(PROMPT_TEMPLATE_KWARG)
        return render_prompt_template(Path(str(value)), instruction) if value else instruction

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = _GateState()
        try:
            await self._drive(self._task(instruction), environment, state)
        except asyncio.CancelledError:
            state.termination_reason = "cancelled"
            raise
        except Exception as exc:
            state.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._finish(context, state)
        if state.failure is not None:
            # Classified by Harbor/collect exactly as the unwrapped agent's error.
            raise state.failure

    async def _drive(self, task: str, environment: BaseEnvironment, state: _GateState) -> None:
        session = await self._session(environment, state, "work", 0, task)
        for round_no in range(self.gate_options.max_rounds + 1):
            if session.error is not None:
                state.termination_reason, state.error = "stage_failed", session.error
                return
            gate = await self._gate(environment, state, session)
            if gate.error is not None:
                state.termination_reason, state.error = "gate_error", gate.error
                return
            if gate.passed:
                state.status, state.termination_reason = "completed", "gate_clean"
                return
            if round_no == self.gate_options.max_rounds:
                break
            prompt = fix_prompt(
                task, gate, self.gate_options.command, self.gate_options.timeout_sec
            )
            session = await self._session(environment, state, "fix", round_no + 1, prompt)
        state.status, state.termination_reason = "completed", "rounds_exhausted"

    async def _gate(
        self, environment: BaseEnvironment, state: _GateState, after: _Session
    ) -> GateRun:
        run = len(state.runs) + 1
        relative = f"{GATE_DIR_NAME}/gate_run_{run}.log"
        container_log = PurePosixPath(str(self.environment_logs_dir)) / relative
        reports = str(report_dir_for(PurePosixPath(relative)))
        options = self.gate_options
        started = time.monotonic()
        result = await environment.exec(
            gate_script(options, run, container_log), timeout_sec=options.timeout_sec + 60
        )
        gate = read_gate_run(
            result.stdout or "", run=run, after=after.name, mode=options.mode, log=relative
        )
        gate = replace(gate, report_dir=reports, duration_sec=round(time.monotonic() - started, 3))
        if gate.exit_code is None and gate.git_exit_code is None:
            stderr = (result.stderr or "")[-500:]
            gate = replace(gate, output=gate.output or stderr or "(gate script failed)")
        if not environment.capabilities.mounted:
            await environment.download_file(str(container_log), self.logs_dir / relative)
            await environment.download_dir(
                str(report_dir_for(container_log)), self.logs_dir / reports
            )
        state.runs.append(gate)
        return gate

    async def _session(
        self, environment: BaseEnvironment, state: _GateState, role: str, round_no: int, prompt: str
    ) -> _Session:
        session = _Session(len(state.sessions) + 1, role, round_no, AgentContext(), Path())
        session.directory = self.logs_dir / GATE_DIR_NAME / session.name
        state.sessions.append(session)
        inner = self._inner(session.directory)
        session.started_at, started = _now(), time.monotonic()
        try:
            await inner.run(prompt, environment, session.context)
        except asyncio.CancelledError:
            session.error = "CancelledError: the agent phase was cancelled (agent timeout)"
            _stop_clock(session, started)
            await asyncio.shield(self._settle(environment, session, inner))
            raise
        except Exception as exc:
            session.error = f"{type(exc).__name__}: {exc}"
            state.failure = state.failure or exc
        _stop_clock(session, started)
        await self._settle(environment, session, inner)
        return session

    async def _settle(
        self, environment: BaseEnvironment, session: _Session, inner: BaseAgent
    ) -> None:
        """File the session's logs under its stage dir and read its usage."""
        logs = shlex.quote(str(self.environment_logs_dir))
        target = f"{GATE_DIR_NAME}/{session.name}"
        keep = " ".join(
            f"! -name {shlex.quote(name)}" for name in (GATE_DIR_NAME, *STEP_LEVEL_LOGS)
        )
        result = await environment.exec(
            f"cd {logs} && mkdir -p {target} && "
            f"find . -mindepth 1 -maxdepth 1 {keep} -exec mv {{}} {target}/ \\;"
        )
        if result.return_code != 0:
            raise RuntimeError(f"guardrail-gate could not file session logs: {result.stderr}")
        if not environment.capabilities.mounted:
            await environment.download_dir(
                str(self.environment_logs_dir / target), session.directory
            )
        if session.context.is_empty():
            inner.populate_context_post_run(session.context)

    def _finish(self, context: AgentContext, state: _GateState) -> None:
        for key, value in sum_usage([session.context for session in state.sessions]).items():
            setattr(context, key, value)
        record = gate_record(self.gate_options, state)
        context.metadata = {**(context.metadata or {}), "guardrail_gate": record}
        gate_dir = self.logs_dir / GATE_DIR_NAME
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / GATE_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(self._root_trajectory(state).to_json_dict(), indent=2) + "\n"
        )

    def _root_trajectory(self, state: _GateState) -> Trajectory:
        subagents: list[Trajectory] = []
        steps: list[dict[str, Any]] = []
        for session in state.sessions:
            ref: dict[str, Any] = {"trajectory_id": session.name}
            path = session.directory / "trajectory.json"
            if path.is_file():
                sub = Trajectory.model_validate_json(path.read_text())
                subagents.append(sub.model_copy(update={"trajectory_id": session.name}))
            else:
                ref = {
                    "trajectory_path": f"{GATE_DIR_NAME}/{session.name}",
                    "extra": {"missing": True},
                }
            steps.append(
                {
                    "step_id": len(steps) + 1,
                    "source": "system",
                    "message": f"guardrail-gate {session.name} (round {session.round})",
                    "observation": {
                        "results": [{"content": session.error, "subagent_trajectory_ref": [ref]}]
                    },
                }
            )
        usage = sum_usage([session.context for session in state.sessions])
        return Trajectory.model_validate(
            {
                "session_id": self.session_id,
                "agent": {"name": self.name(), "version": VERSION, "model_name": self.model_name},
                "steps": steps
                or [{"step_id": 1, "source": "system", "message": "guardrail-gate ran no session"}],
                "final_metrics": {
                    "total_prompt_tokens": usage["n_input_tokens"],
                    "total_completion_tokens": usage["n_output_tokens"],
                    "total_cached_tokens": usage["n_cache_tokens"],
                    "total_cost_usd": usage["cost_usd"],
                    "total_steps": len(state.sessions),
                },
                "subagent_trajectories": subagents or None,
            }
        )


def _stop_clock(session: _Session, started: float) -> None:
    """The session's end and its monotonic duration (the inner agent's run only)."""
    session.finished_at = _now()
    session.duration_sec = round(time.monotonic() - started, 3)


def _usage_gaps(sessions: list[_Session]) -> dict[str, list[str]]:
    """For each usage field, the sessions that did not report it (review-loop's rule)."""
    gaps = {
        key: [s.name for s in sessions if getattr(s.context, key) is None] for key in USAGE_FIELDS
    }
    return {key: names for key, names in gaps.items() if names}


def gate_record(options: GuardrailGateOptions, state: _GateState) -> dict[str, Any]:
    runs = [run.record() for run in state.runs]
    last = runs[-1] if runs else None
    return {
        "workflow": options.component,
        "mode": options.mode.value,
        "command": options.command,
        "status": state.status,
        "termination_reason": state.termination_reason,
        "error": state.error,
        "rounds_configured": options.max_rounds,
        "fix_rounds_used": sum(1 for session in state.sessions if session.role == "fix"),
        "gate_runs": runs,
        "final_checks_failed": last["checks_failed"] if last else None,
        SIGNAL: state.clean_at_end,
        "repo_created": any(run.repo_created for run in state.runs),
        "stages": [session.record() for session in state.sessions],
        "usage_unavailable": _usage_gaps(state.sessions),
    }
