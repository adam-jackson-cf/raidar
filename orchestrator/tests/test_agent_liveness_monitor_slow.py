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
from tests.test_agent_liveness_monitor import ScriptedEnv, fake_trial, poll_output, stat_line

pytestmark = pytest.mark.slow


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
