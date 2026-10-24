"""``guardrail-gate`` workflow agent: the repository refuses work its commit gate fails.

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

- ``mode: shadow``: one work session, then the same commit check as ``commit``
  mode, run once at the step's end in a git directory outside the workspace
  (``raidar.components.runtime.gate_run``). The full gate run is recorded, but the
  gate never refuses: no fix session starts, nothing is written to the
  workspace and the agent is never told. The record's termination reason is
  ``shadow_recorded`` (``gate_error`` when the gate could not decide) and its
  manipulation-check signal ``gate_clean_at_step_end`` is ``None`` (the gate
  enforced nothing); ``shadow_verdict`` holds what the gate decided.
- git evidence for every commit attempt (``commit`` and ``shadow``): each gate
  run's ``git`` block and the step's ``git_evidence`` (the last attempt's
  repository state, every hook exit code and every ``git commit`` exit code),
  so a refusal is verifiable from git itself.
- **pre-fix snapshots.** Before every fix session the workspace (``/app``) is
  snapshotted with the verifier's own artifact mechanism and excludes
  (``download_dir_with_exclusions`` with ``SNAPSHOT_EXCLUDES``) into
  ``guardrail-gate/pre_fix_<round>/app`` under the step's agent logs, outside
  the workspace, and its ``tree_digest`` recorded in the gate record
  (``pre_fix_snapshots``). A shadow gate starts no fix session and so takes no
  pre-fix snapshot. A snapshot that fails is recorded with its error and never
  fails the step.
- **a nested inner agent.** ``inner_agent`` may be a session wrapper given by
  ``import_path`` (``continue-on-stall``; ``raidar.components.runtime.session_chain``)
  around the native agent. The gate renders the task with the chain's prompt
  template once and runs every session through the chain.
- **autofix.** ``autofix_before_check`` (default false) with ``autofix_command``,
  the pack's safe-fix command (``raidar.components.runtime.autofix_run`` states
  its contract). In ``commit`` mode (enforced) the command runs in the workspace
  before every gate run, then the gate stages and commits as usual; a failed
  autofix pass is typed and recorded and that gate run runs without it. In
  ``shadow`` mode (recorded, never enforced) the command runs, after the raw
  shadow run, on a throwaway copy of the workspace outside it, and the same
  shadow check runs on the fixed copy (``post_check``); the workspace stays
  byte-identical and the copy is deleted. Every pass is recorded under
  ``autofix.runs`` of the gate record.

Recorded in ``/logs/agent/guardrail-gate/guardrail-gate.json`` and in the
step's ``AgentContext.metadata["guardrail_gate"]``: every gate run (exit codes,
checks failed and passed when the output's format is recognised, the output's
sha256 and size, the full output log, its duration, its report dir and git
evidence), every session's usage, start, end and duration, the fix rounds used,
the termination reason, and the deterministic manipulation-check signal
``gate_clean_at_step_end`` (``True``/``False``; ``None`` when no gate run
completed or in shadow mode). Every session's tokens and cost are summed into
the step's context, so the arm pays for its fix sessions, and every session's
trajectory is embedded in the step's root ``trajectory.json`` (as review-loop does).

Each gate run exports ``RAIDAR_GATE_RUN`` (its number) and
``RAIDAR_GATE_REPORT_DIR`` (``/logs/agent/guardrail-gate/gate_run_<n>``, created
empty) to the command, so a pack's gate can write a machine-readable report per
run (``guardrail-check --json-dir "$RAIDAR_GATE_REPORT_DIR"``); ``gate-effort``
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
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from raidar.components.runtime.autofix_run import (
    AUTOFIX_LOG,
    POST_CHECK_LOG,
    AutofixMode,
    AutofixRun,
    autofix_script,
    cleanup_script,
    copy_gate_dir,
    copy_root,
    read_autofix_run,
)
from raidar.components.runtime.gate_run import (
    GATE_DIR_NAME,
    GATE_RECORD,
    SHADOW_TERMINATION,
    SIGNAL,
    VERSION,
    GateRun,
    GitEvidence,
    GuardrailGateOptions,
    fix_prompt,
    gate_script,
    read_gate_run,
    report_dir_for,
)
from raidar.components.runtime.review_loop import (
    STEP_LEVEL_LOGS,
    USAGE_FIELDS,
    sum_usage,
    usage_gaps,
)
from raidar.components.runtime.session_chain import create_inner, render_task
from raidar.core.snapshot import APP_SOURCE, SNAPSHOT_EXCLUDES, snapshot_files, tree_digest

PRE_FIX_DIR = "pre_fix_{round}"
SNAPSHOT_DIR = "app"
PRE_FIX_KEY = "pre_fix_snapshots"
AUTOFIX_KEY = "autofix"

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


@dataclass(frozen=True)
class PreFixSnapshot:
    """The workspace a fix session started from."""

    round: int
    before_session: str
    path: str
    digest: str | None
    files: int | None
    error: str | None = None

    def record(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "before_session": self.before_session,
            "path": self.path,
            "tree_digest": self.digest,
            "files": self.files,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class _GateState:
    sessions: list[_Session] = field(default_factory=list)
    runs: list[GateRun] = field(default_factory=list)
    pre_fix: list[PreFixSnapshot] = field(default_factory=list)
    autofix: list[AutofixRun] = field(default_factory=list)
    shadow: bool = False
    status: str = "failed"
    termination_reason: str = "loop_exception"
    error: str | None = None
    failure: BaseException | None = None

    @property
    def clean_at_end(self) -> bool | None:
        """The last gate run's verdict, if it decided and no session ran after it.

        ``None`` in shadow mode: a gate that never refuses enforced nothing.
        """
        if self.shadow or not self.runs or len(self.sessions) > len(self.runs):
            return None
        if self.runs[-1].error:
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
        return create_inner(
            self, self.gate_options.inner_agent, logs_dir, model_name=self.model_name
        )

    def _task(self, instruction: str) -> str:
        return render_task(self.gate_options.inner_agent, instruction)

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._inner(self.logs_dir).setup(environment)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state = _GateState(shadow=self.gate_options.shadow)
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
        if self.gate_options.shadow:
            await self._drive_shadow(task, environment, state)
        else:
            await self._drive_enforced(task, environment, state)

    async def _drive_enforced(
        self, task: str, environment: BaseEnvironment, state: _GateState
    ) -> None:
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

    async def _drive_shadow(
        self, task: str, environment: BaseEnvironment, state: _GateState
    ) -> None:
        session = await self._session(environment, state, "work", 0, task)
        if session.error is not None:
            state.termination_reason, state.error = "stage_failed", session.error
            return
        gate = await self._gate(environment, state, session)
        if gate.error is not None:
            state.termination_reason, state.error = "gate_error", gate.error
            return
        state.status, state.termination_reason = "completed", SHADOW_TERMINATION
        if self.gate_options.autofix:
            await self._shadow_autofix(environment, state, session)

    async def _gate(
        self, environment: BaseEnvironment, state: _GateState, after: _Session
    ) -> GateRun:
        options = self.gate_options
        if options.autofix and not options.shadow:
            before = len(state.runs) + 1
            run = await self._autofix(environment, state, after, AutofixMode.APPLY, before)
            if not run.ok:
                self.logger.warning(
                    "guardrail-gate autofix %s failed (%s): %s; gate run %s runs without it",
                    run.run,
                    run.error_type,
                    run.error,
                    before,
                )
        return await self._check(environment, state, after)

    async def _check(
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
        await self._fetch(environment, container_log, relative)
        state.runs.append(gate)
        return gate

    async def _session(
        self, environment: BaseEnvironment, state: _GateState, role: str, round_no: int, prompt: str
    ) -> _Session:
        if role == "fix":
            before = f"stage_{len(state.sessions) + 1}_fix"
            state.pre_fix.append(await self._snapshot(environment, round_no, before))
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

    async def _snapshot(
        self, environment: BaseEnvironment, round_no: int, before: str
    ) -> PreFixSnapshot:
        relative = f"{GATE_DIR_NAME}/{PRE_FIX_DIR.format(round=round_no)}/{SNAPSHOT_DIR}"
        target = self.logs_dir / relative
        try:
            await environment.download_dir_with_exclusions(
                source_dir=APP_SOURCE, target_dir=target, exclude=list(SNAPSHOT_EXCLUDES)
            )
            return PreFixSnapshot(
                round_no, before, relative, tree_digest(target), len(snapshot_files(target))
            )
        except Exception as exc:  # noqa: BLE001 - recorded; a missing snapshot is a gap
            return PreFixSnapshot(
                round_no, before, relative, None, None, f"{type(exc).__name__}: {exc}"
            )

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

    async def _fetch(
        self, environment: BaseEnvironment, container_log: PurePosixPath, relative: str
    ) -> None:
        """Download a pass's log and report dir when the logs dir is not mounted."""
        if environment.capabilities.mounted:
            return
        await environment.download_file(str(container_log), self.logs_dir / relative)
        await environment.download_dir(
            str(report_dir_for(container_log)),
            self.logs_dir / str(report_dir_for(PurePosixPath(relative))),
        )

    # ---- one autofix pass ---------------------------------------------------------------

    def _paths(self, run: int, template: str) -> tuple[str, PurePosixPath]:
        relative = f"{GATE_DIR_NAME}/{template.format(run=run)}"
        return relative, PurePosixPath(str(self.environment_logs_dir)) / relative

    async def _autofix(
        self,
        environment: BaseEnvironment,
        state: _GateState,
        after: _Session,
        mode: AutofixMode,
        gate_run: int,
    ) -> AutofixRun:
        options = self.gate_options
        number = len(state.autofix) + 1
        relative, container_log = self._paths(number, AUTOFIX_LOG)
        base = AutofixRun(
            run=number,
            after=after.name,
            mode=mode,
            log=relative,
            report_dir=str(PurePosixPath(relative).with_suffix("")),
            before_gate_run=gate_run if mode is AutofixMode.APPLY else None,
            after_gate_run=gate_run if mode is AutofixMode.DRY_RUN else None,
        )
        script = autofix_script(
            str(options.autofix_command),
            mode,
            number,
            container_log,
            options.work_dir,
            options.timeout_sec,
        )
        started = time.monotonic()
        try:
            result = await environment.exec(script, timeout_sec=options.timeout_sec + 60)
            run = read_autofix_run(result.stdout or "", base)
            await self._fetch(environment, container_log, relative)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - typed and recorded; the gate falls back
            run = replace(base, error_type="exec_failed", error=f"{type(exc).__name__}: {exc}")
        run = replace(run, duration_sec=round(time.monotonic() - started, 3))
        state.autofix.append(run)
        return run

    # ---- shadow: the same pass and check on a throwaway copy -----------------------------

    async def _shadow_autofix(
        self, environment: BaseEnvironment, state: _GateState, after: _Session
    ) -> None:
        run = await self._autofix(environment, state, after, AutofixMode.DRY_RUN, len(state.runs))
        try:
            if run.ok:
                check = await self._post_check(environment, run, after)
                state.autofix[-1] = replace(run, post_check=check)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded as an undecided check, never fatal
            error = f"the post-autofix check could not run: {type(exc).__name__}: {exc}"
            state.autofix[-1] = replace(run, post_check={"run": run.run, "error": error})
        finally:
            await self._cleanup(environment)

    async def _cleanup(self, environment: BaseEnvironment) -> None:
        """Delete the copy (the per-step reset also owns the work dir)."""
        try:
            await environment.exec(cleanup_script(self.gate_options.work_dir))
        except Exception as exc:  # noqa: BLE001 - the step reset removes it anyway
            self.logger.warning("guardrail-gate could not delete the autofix copy: %s", exc)

    async def _post_check(
        self, environment: BaseEnvironment, run: AutofixRun, after: _Session
    ) -> dict[str, Any]:
        """The shadow check in the fixed copy (its own hook and shadow git dir)."""
        options = self.gate_options
        copy_options = options.model_copy(update={"work_dir": str(copy_gate_dir(options.work_dir))})
        relative, container_log = self._paths(run.run, POST_CHECK_LOG)
        root = copy_root(options.work_dir)
        started = time.monotonic()
        result = await environment.exec(
            f"cd {root} && {{\n{gate_script(copy_options, run.run, container_log)}}}",
            timeout_sec=options.timeout_sec + 60,
        )
        gate = read_gate_run(
            result.stdout or "", run=run.run, after=after.name, mode=options.mode, log=relative
        )
        gate = replace(
            gate,
            report_dir=str(PurePosixPath(relative).with_suffix("")),
            duration_sec=round(time.monotonic() - started, 3),
        )
        await self._fetch(environment, container_log, relative)
        return {**gate.record(), "root": str(root)}

    # ---- the record ------------------------------------------------------------------

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


def _git_evidence(runs: list[GateRun]) -> dict[str, Any] | None:
    attempts = [run for run in runs if run.git is not None]
    if not attempts:
        return None
    last = cast(GitEvidence, attempts[-1].git)
    return {
        **last.record(),
        "hook_exit_codes": [run.exit_code for run in attempts],
        "git_commit_exit_codes": [run.git_exit_code for run in attempts],
        "commits_recorded": sum(1 for run in attempts if run.git and run.git.head_moved),
    }


def _shadow_verdict(state: _GateState, runs: list[dict[str, Any]]) -> bool | None:
    """What a shadow gate decided on its one run (``None``: not shadow, or undecided)."""
    if not state.shadow or not runs or runs[0]["error"] is not None:
        return None
    return cast(bool, runs[0]["passed"])


def _autofix_block(options: GuardrailGateOptions, state: _GateState) -> dict[str, Any]:
    mode = AutofixMode.DRY_RUN if options.shadow else AutofixMode.APPLY
    return {
        "enabled": options.autofix,
        "command": options.autofix_command if options.autofix else None,
        "mode": mode.value if options.autofix else None,
        "runs": [run.record() for run in state.autofix],
        "errors": sum(1 for run in state.autofix if not run.ok),
    }


def gate_record(options: GuardrailGateOptions, state: _GateState) -> dict[str, Any]:
    runs = [run.record() for run in state.runs]
    last = runs[-1] if runs else None
    return {
        "workflow": options.component,
        "mode": options.mode.value,
        "shadow": state.shadow,
        "command": options.command,
        "status": state.status,
        "termination_reason": state.termination_reason,
        "error": state.error,
        "rounds_configured": options.max_rounds,
        "fix_rounds_used": sum(1 for session in state.sessions if session.role == "fix"),
        "gate_runs": runs,
        "final_checks_failed": last["checks_failed"] if last else None,
        SIGNAL: state.clean_at_end,
        "shadow_verdict": _shadow_verdict(state, runs),
        "git_evidence": _git_evidence(state.runs),
        "repo_created": any(run.repo_created for run in state.runs),
        "stages": [session.record() for session in state.sessions],
        "usage_unavailable": usage_gaps((s.name, s.context) for s in state.sessions),
        PRE_FIX_KEY: [snapshot.record() for snapshot in state.pre_fix],
        AUTOFIX_KEY: _autofix_block(options, state),
    }


__all__ = ["GuardrailGateAgent", "PreFixSnapshot", "gate_record"]
