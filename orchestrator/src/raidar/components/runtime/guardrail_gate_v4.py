"""``guardrail-gate@4`` workflow agent: guardrail-gate@3 with an optional autofix step.

guardrail-gate@1-@3 are frozen. @4 keeps @3's loop, modes, prompts, git
evidence, pre-fix snapshots, nested inner agent, billing and record, and adds
``autofix_before_check`` (default false: @4 then behaves exactly as @3) with
``autofix_command``, the pack's safe-fix command
(``raidar.components.runtime.autofix_run`` states its contract):

- **commit mode** (enforced): before every gate run (the step-end run and each
  re-check after a fix session) the command runs in the workspace, then the
  gate stages everything (``git add -A``) and commits with the gate command as
  the pre-commit hook exactly as @3. This is the common lint-staged/pre-commit
  autofix practice of a real repository, and harness-agnostic (no Claude or
  Codex hook). If the autofix pass fails, the error is typed and recorded and
  that gate run is @3's (``fallback``); it is never silent;
- **shadow mode** (recorded, never enforced): after @3's shadow run, the
  command runs on a throwaway copy of the workspace outside it, and the same
  shadow check runs on the fixed copy (``post_check``: the first-run findings
  after autofix, beside the raw ones of ``gate_runs``). The workspace stays
  byte-identical and the agent sees nothing; the copy is deleted.

Every pass is recorded under ``autofix.runs`` of the gate record (files and
lines changed, codes fixed, all and block-tier, from the command's report; its
log and report dir under ``/logs/agent/guardrail-gate/autofix_run_<n>``; a
shadow pass's check under ``post_autofix_run_<n>``). ``gate_runs`` keeps @3's
meaning: the commits (enforced) or the raw shadow check (shadow).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, cast

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

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
from raidar.components.runtime.gate_run import GATE_DIR_NAME, GATE_RECORD, GateRun
from raidar.components.runtime.gate_run_v2 import SHADOW_TERMINATION, gate_script, read_gate_run
from raidar.components.runtime.guardrail_gate import _GateState, _Session
from raidar.components.runtime.guardrail_gate_v3 import (
    GuardrailGateOptionsV3,
    GuardrailGateV3Agent,
    _GateStateV3,
    gate_record_v3,
)
from raidar.components.runtime.review_loop import sum_usage

COMPONENT = "guardrail-gate@4"
VERSION = "4"
AUTOFIX_KEY = "autofix"


class GuardrailGateOptionsV4(GuardrailGateOptionsV3):
    autofix_before_check: bool = False
    autofix_command: str | None = None
    component: str = COMPONENT

    @property
    def autofix(self) -> bool:
        return self.autofix_before_check and bool(self.autofix_command)


@dataclass
class _GateStateV4(_GateStateV3):
    autofix: list[AutofixRun] = field(default_factory=list)


class GuardrailGateV4Agent(GuardrailGateV3Agent):
    """guardrail-gate@3 with the pack's safe auto-fixers before each check (or on a copy)."""

    options_model = GuardrailGateOptionsV4

    def version(self) -> str | None:
        return VERSION

    @property
    def gate_options(self) -> GuardrailGateOptionsV4:
        return cast(GuardrailGateOptionsV4, self.options)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """@3's run with the v4 state (autofix passes)."""
        state = _GateStateV4(shadow=self.gate_options.shadow)
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
            raise state.failure

    async def _drive(self, task: str, environment: BaseEnvironment, state: _GateState) -> None:
        await super()._drive(task, environment, state)
        options = self.gate_options
        if not (options.shadow and options.autofix):
            return
        if state.termination_reason != SHADOW_TERMINATION or not state.runs:
            return  # the raw shadow check did not decide: nothing to compare with
        await self._shadow_autofix(environment, cast(_GateStateV4, state), state.sessions[-1])

    async def _gate(
        self, environment: BaseEnvironment, state: _GateState, after: _Session
    ) -> GateRun:
        options = self.gate_options
        if options.autofix and not options.shadow:
            before = len(state.runs) + 1
            run = await self._autofix(
                environment, cast(_GateStateV4, state), after, AutofixMode.APPLY, before
            )
            if not run.ok:
                self.logger.warning(
                    "guardrail-gate@4 autofix %s failed (%s): %s; gate run %s runs as @3",
                    run.run,
                    run.error_type,
                    run.error,
                    before,
                )
        return await super()._gate(environment, state, after)

    # ---- one autofix pass ---------------------------------------------------------------

    def _paths(self, run: int, template: str) -> tuple[str, PurePosixPath]:
        relative = f"{GATE_DIR_NAME}/{template.format(run=run)}"
        return relative, PurePosixPath(str(self.environment_logs_dir)) / relative

    async def _autofix(
        self,
        environment: BaseEnvironment,
        state: _GateStateV4,
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

    async def _fetch(
        self, environment: BaseEnvironment, container_log: PurePosixPath, relative: str
    ) -> None:
        if environment.capabilities.mounted:
            return
        await environment.download_file(str(container_log), self.logs_dir / relative)
        await environment.download_dir(
            str(container_log.with_suffix("")),
            self.logs_dir / str(PurePosixPath(relative).with_suffix("")),
        )

    # ---- shadow: the same pass and check on a throwaway copy -----------------------------

    async def _shadow_autofix(
        self, environment: BaseEnvironment, state: _GateStateV4, after: _Session
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
            self.logger.warning("guardrail-gate@4 could not delete the autofix copy: %s", exc)

    async def _post_check(
        self, environment: BaseEnvironment, run: AutofixRun, after: _Session
    ) -> dict[str, Any]:
        """@3's shadow check in the fixed copy (its own hook and shadow git dir)."""
        options = self.gate_options
        copy_options = options.model_copy(update={"work_dir": str(copy_gate_dir(options.work_dir))})
        relative, container_log = self._paths(run.run, POST_CHECK_LOG)
        root = copy_root(options.work_dir)
        started = time.monotonic()
        result = await environment.exec(
            f"cd {root} && {{\n{gate_script(copy_options, run.run, container_log)}}}",
            timeout_sec=options.timeout_sec + 60,
        )
        detailed = read_gate_run(
            result.stdout or "", run=run.run, after=after.name, mode=options.mode, log=relative
        )
        gate = replace(
            detailed.gate,
            report_dir=str(PurePosixPath(relative).with_suffix("")),
            duration_sec=round(time.monotonic() - started, 3),
        )
        await self._fetch(environment, container_log, relative)
        return {**replace(detailed, gate=gate).record(), "root": str(root)}

    # ---- the record ------------------------------------------------------------------

    def _finish(self, context: AgentContext, state: _GateState) -> None:
        """@3's step record and root trajectory, as guardrail-gate@4."""
        for key, value in sum_usage([session.context for session in state.sessions]).items():
            setattr(context, key, value)
        record = gate_record_v4(self.gate_options, cast(_GateStateV4, state))
        context.metadata = {**(context.metadata or {}), "guardrail_gate": record}
        gate_dir = self.logs_dir / GATE_DIR_NAME
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / GATE_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        trajectory = self._root_trajectory(state)
        agent = trajectory.agent.model_copy(update={"version": VERSION})
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.model_copy(update={"agent": agent}).to_json_dict(), indent=2)
            + "\n"
        )


def gate_record_v4(options: GuardrailGateOptionsV4, state: _GateStateV4) -> dict[str, Any]:
    """@3's record with the autofix passes."""
    record = gate_record_v3(options, state)
    mode = AutofixMode.DRY_RUN if options.shadow else AutofixMode.APPLY
    record[AUTOFIX_KEY] = {
        "enabled": options.autofix,
        "command": options.autofix_command if options.autofix else None,
        "mode": mode.value if options.autofix else None,
        "runs": [run.record() for run in state.autofix],
        "errors": sum(1 for run in state.autofix if not run.ok),
    }
    return record


__all__ = ["COMPONENT", "VERSION", "GuardrailGateOptionsV4", "GuardrailGateV4Agent"]
