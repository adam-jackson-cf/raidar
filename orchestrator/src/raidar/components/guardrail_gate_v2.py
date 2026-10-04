"""``guardrail-gate@2`` workflow agent: guardrail-gate@1 with a shadow mode and git evidence.

guardrail-gate@1 (``raidar.components.guardrail_gate``) is frozen. @2 keeps its
loop, sessions, prompts, log filing, billing and record, and adds:

- ``mode: shadow``: one work session, then the same commit check as ``commit``
  mode, run once at the step's end in a git directory outside the workspace
  (``raidar.components.gate_run_v2``). The full gate run is recorded, but the
  gate never refuses: no fix session starts, nothing is written to the
  workspace and the agent is never told. The record's termination reason is
  ``shadow_recorded`` (``gate_error`` when the gate could not decide) and its
  manipulation-check signal ``gate_clean_at_step_end`` is ``None`` (the gate
  enforced nothing); ``shadow_verdict`` holds what the gate decided. It gives
  a baseline arm the gate's first-run findings without changing its agent's
  experience, so gate-effort outcomes pair across arms (``gate-effort@2``);
- git evidence for every commit attempt (``commit`` and ``shadow``): each gate
  run's ``git`` block (``HEAD`` before and after, ``git log --oneline``, ``git
  status --porcelain``), and the step's ``git_evidence`` (the last attempt's
  repository state, every hook exit code and every ``git commit`` exit code),
  written to the gate record under ``/logs/agent/guardrail-gate`` (outside the
  workspace), so a refusal is verifiable from git itself.
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

from raidar.components.gate_run import GATE_DIR_NAME, GATE_RECORD, SIGNAL, GateRun
from raidar.components.gate_run_v2 import (
    SHADOW_TERMINATION,
    VERSION,
    GateRunV2,
    GuardrailGateOptionsV2,
    gate_script,
    read_gate_run,
)
from raidar.components.guardrail_gate import (
    GuardrailGateAgent,
    _GateState,
    _Session,
    gate_record,
)
from raidar.components.review_loop import sum_usage


@dataclass
class _GateStateV2(_GateState):
    detailed: list[GateRunV2] = field(default_factory=list)
    shadow: bool = False

    @property
    def clean_at_end(self) -> bool | None:
        """``None`` in shadow mode: a gate that never refuses enforced nothing."""
        return None if self.shadow else super().clean_at_end


class GuardrailGateV2Agent(GuardrailGateAgent):
    """guardrail-gate@1's loop with a shadow mode and git evidence in the record."""

    options_model = GuardrailGateOptionsV2

    def version(self) -> str | None:
        return VERSION

    @property
    def gate_options(self) -> GuardrailGateOptionsV2:
        return cast(GuardrailGateOptionsV2, self.options)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """@1's run with the v2 state (a shadow flag and every run's git evidence)."""
        state = _GateStateV2(shadow=self.gate_options.shadow)
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
        if not self.gate_options.shadow:
            await super()._drive(task, environment, state)
            return
        session = await self._session(environment, state, "work", 0, task)
        if session.error is not None:
            state.termination_reason, state.error = "stage_failed", session.error
            return
        gate = await self._gate(environment, state, session)
        if gate.error is not None:
            state.termination_reason, state.error = "gate_error", gate.error
            return
        state.status, state.termination_reason = "completed", SHADOW_TERMINATION

    async def _gate(
        self, environment: BaseEnvironment, state: _GateState, after: _Session
    ) -> GateRun:
        detailed_runs = cast(_GateStateV2, state).detailed
        options = self.gate_options
        run = len(state.runs) + 1
        relative = f"{GATE_DIR_NAME}/gate_run_{run}.log"
        container_log = PurePosixPath(str(self.environment_logs_dir)) / relative
        reports = f"{GATE_DIR_NAME}/gate_run_{run}"
        started = time.monotonic()
        result = await environment.exec(
            gate_script(options, run, container_log), timeout_sec=options.timeout_sec + 60
        )
        detailed = read_gate_run(
            result.stdout or "", run=run, after=after.name, mode=options.mode, log=relative
        )
        gate = replace(
            detailed.gate, report_dir=reports, duration_sec=round(time.monotonic() - started, 3)
        )
        if gate.exit_code is None and gate.git_exit_code is None:
            stderr = (result.stderr or "")[-500:]
            gate = replace(gate, output=gate.output or stderr or "(gate script failed)")
        if not environment.capabilities.mounted:
            await environment.download_file(str(container_log), self.logs_dir / relative)
            await environment.download_dir(
                str(container_log.with_suffix("")), self.logs_dir / reports
            )
        state.runs.append(gate)
        detailed_runs.append(replace(detailed, gate=gate))
        return gate

    def _finish(self, context: AgentContext, state: _GateState) -> None:
        for key, value in sum_usage([session.context for session in state.sessions]).items():
            setattr(context, key, value)
        record = gate_record_v2(self.gate_options, cast(_GateStateV2, state))
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


def _git_evidence(runs: list[GateRunV2]) -> dict[str, Any] | None:
    attempts = [run for run in runs if run.git is not None]
    if not attempts:
        return None
    last = attempts[-1].git
    assert last is not None
    return {
        **last.record(),
        "hook_exit_codes": [run.gate.exit_code for run in attempts],
        "git_commit_exit_codes": [run.gate.git_exit_code for run in attempts],
        "commits_recorded": sum(1 for run in attempts if run.git and run.git.head_moved),
    }


def gate_record_v2(options: GuardrailGateOptionsV2, state: _GateStateV2) -> dict[str, Any]:
    """@1's record with the v2 mode, every run's git block and the step's git evidence."""
    record = gate_record(options, state)
    runs = [run.record() for run in state.detailed]
    record["gate_runs"] = runs
    record["mode"] = options.mode.value
    record["shadow"] = state.shadow
    record[SIGNAL] = state.clean_at_end
    record["shadow_verdict"] = (
        (runs[0]["passed"] if runs and runs[0]["error"] is None else None) if state.shadow else None
    )
    record["git_evidence"] = _git_evidence(state.detailed)
    return record
