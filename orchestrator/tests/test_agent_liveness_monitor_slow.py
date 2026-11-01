"""Liveness cancellation and host-report seams that wait on real clocks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from harbor.trial.hooks import TrialEvent

from raidar.engine.health.liveness import LIVENESS_REPORT, LivenessConfig, install_liveness_monitor
from raidar.engine.health.reaper import _KILLED, _RESIDUAL
from tests.test_agent_liveness_monitor import (
    Clock,
    ScriptedEnv,
    fake_trial,
    monitor,
    poll_output,
    run_samples,
    stat_line,
)

pytestmark = pytest.mark.slow


def test_a_cancel_during_the_kill_still_records_the_idle_kill() -> None:
    """The kill ends the agent, so the phase-end hook cancels the monitor mid-kill."""
    quiet = poll_output([stat_line(5, "sleep", 3)])
    kill_output = f"{_KILLED}\n5 1000 S sleep\n{_RESIDUAL}\n"
    release = asyncio.Event()

    class SlowKill(ScriptedEnv):
        async def exec(self, command: str, **kwargs: Any) -> Any:
            if "kill -KILL" in command:
                await release.wait()
            return await super().exec(command, **kwargs)

    clock = Clock()
    m = monitor(SlowKill([quiet] * 4 + [kill_output]), clock, mode="enforce", idle_s=45)
    run_samples(m, clock, 3)

    async def go() -> None:
        task = asyncio.ensure_future(m.sample())
        await asyncio.sleep(0.01)  # the sample is inside the kill
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    assert m.verdict.decision == "idle_killed"
    assert m.verdict.kill is not None


def test_hooks_arm_the_phase_and_write_the_host_side_report(tmp_path: Path) -> None:
    env = ScriptedEnv([poll_output([stat_line(5, "sleep", 3)])])
    trial, hooks, callbacks = fake_trial(tmp_path, env)
    config = LivenessConfig("observe", interval_s=0.01, idle_s=0.03)
    install_liveness_monitor(cast(Any, trial), config)
    event = SimpleNamespace(
        result=SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_1")])
    )

    async def phase() -> None:
        await hooks[TrialEvent.AGENT_START][0](event)
        await callbacks[0](SimpleNamespace(phase="agent_setup"))  # outside the phase: ignored
        await asyncio.sleep(0.3)
        await callbacks[0](SimpleNamespace(phase="agent"))
        await hooks[TrialEvent.AGENT_END][0](event)

    asyncio.run(phase())
    report = json.loads((tmp_path / "trial" / LIVENESS_REPORT).read_text())
    (recorded,) = report["phases"]
    assert recorded["step"] == "checkpoint_1"
    assert recorded["decision"] == "idle_observed"
    assert report["config"]["mode"] == "observe"
    assert recorded["samples"][0]["baseline"]
