"""``guardrail-gate@3`` workflow agent: guardrail-gate@2 that keeps the tree each fix started from.

guardrail-gate@1 and @2 are frozen. @3 keeps @2's loop, modes, prompts, git
evidence, billing and record, and adds:

- **pre-fix snapshots.** Before every fix session the workspace (``/app``) is
  snapshotted with the verifier's own artifact mechanism and excludes
  (``download_dir_with_exclusions`` with ``SNAPSHOT_EXCLUDES``) into
  ``guardrail-gate/pre_fix_<round>/app`` under the step's agent logs, outside
  the workspace, and its ``tree_digest`` recorded in the gate record
  (``pre_fix_snapshots``). Offline grading (``raidar.verifier.prefix_grade``)
  grades each one with the step's verifier bundle, so the case pass rate a fix
  round started from can be compared with the step's final snapshot
  (``gate-effort@4`` ``fix_round_correctness_delta``). A shadow gate starts no
  fix session and so takes no pre-fix snapshot. A snapshot that fails is
  recorded with its error and never fails the step;
- **a nested inner agent.** ``inner_agent`` may be a session wrapper given by
  ``import_path`` (``continue-on-stall@1``; ``raidar.components.runtime.session_chain``)
  around the native agent. The gate renders the task with the chain's prompt
  template once and runs every session through the chain, so a session
  wrapper's continuation happens inside the session, before the gate runs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.components.runtime.gate_run import GATE_DIR_NAME, GATE_RECORD
from raidar.components.runtime.gate_run_v2 import GuardrailGateOptionsV2
from raidar.components.runtime.guardrail_gate import _GateState, _Session
from raidar.components.runtime.guardrail_gate_v2 import (
    GuardrailGateV2Agent,
    _GateStateV2,
    gate_record_v2,
)
from raidar.components.runtime.review_loop import sum_usage
from raidar.components.runtime.session_chain import InnerAgentSpec, create_inner, render_task
from raidar.core.snapshot import APP_SOURCE, SNAPSHOT_EXCLUDES, snapshot_files, tree_digest

COMPONENT = "guardrail-gate@3"
VERSION = "3"
PRE_FIX_DIR = "pre_fix_{round}"
SNAPSHOT_DIR = "app"
PRE_FIX_KEY = "pre_fix_snapshots"


class GuardrailGateOptionsV3(GuardrailGateOptionsV2):
    inner_agent: InnerAgentSpec  # type: ignore[assignment]
    component: str = COMPONENT


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


@dataclass
class _GateStateV3(_GateStateV2):
    pre_fix: list[PreFixSnapshot] = field(default_factory=list)


class GuardrailGateV3Agent(GuardrailGateV2Agent):
    """guardrail-gate@2's loop with pre-fix snapshots and a nestable inner agent."""

    options_model = GuardrailGateOptionsV3

    def version(self) -> str | None:
        return VERSION

    @property
    def gate_options(self) -> GuardrailGateOptionsV3:
        return cast(GuardrailGateOptionsV3, self.options)

    def _inner(self, logs_dir: Path) -> BaseAgent:
        return create_inner(
            self, self.gate_options.inner_agent, logs_dir, model_name=self.model_name
        )

    def _task(self, instruction: str) -> str:
        return render_task(self.gate_options.inner_agent, instruction)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """@2's run with the v3 state (pre-fix snapshots)."""
        state = _GateStateV3(shadow=self.gate_options.shadow)
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

    async def _session(
        self, environment: BaseEnvironment, state: _GateState, role: str, round_no: int, prompt: str
    ) -> _Session:
        if role == "fix":
            before = f"stage_{len(state.sessions) + 1}_fix"
            snapshot = await self._snapshot(environment, round_no, before)
            cast(_GateStateV3, state).pre_fix.append(snapshot)
        return await super()._session(environment, state, role, round_no, prompt)

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

    def _finish(self, context: AgentContext, state: _GateState) -> None:
        """@2's step record and root trajectory, as guardrail-gate@3."""
        for key, value in sum_usage([session.context for session in state.sessions]).items():
            setattr(context, key, value)
        record = gate_record_v3(self.gate_options, cast(_GateStateV3, state))
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


def gate_record_v3(options: GuardrailGateOptionsV3, state: _GateStateV3) -> dict[str, Any]:
    """@2's record with the pre-fix snapshots."""
    record = gate_record_v2(options, state)
    record[PRE_FIX_KEY] = [snapshot.record() for snapshot in state.pre_fix]
    return record
