"""Liveness monitor against real Harbor trials (spike; opt in with ``RAIDAR_EXECUTION_DOCKER=1``).

``DOCKER_CONTEXT`` selects the engine (``mbp-fwd`` for the remote daemon; set
``RAIDAR_COPY_MODE=1`` there). Thresholds are short so each trial lasts about a minute.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.execution.liveness import LIVENESS_REPORT, LivenessConfig, PhaseMonitor
from raidar.packs.python.image import image_tag
from raidar.packs.python.models import load_pack_spec
from tests.liveness_support import AFTER_PHASE_REPORT, DETACHED_MARKER, run_probe_trial

pytestmark = pytest.mark.skipif(
    os.environ.get("RAIDAR_EXECUTION_DOCKER") != "1",
    reason="set RAIDAR_EXECUTION_DOCKER=1 (and DOCKER_CONTEXT) to run real trials",
)


def _context() -> str:
    return os.environ.get("DOCKER_CONTEXT") or "mbp-fwd"


def _phase(trial_dir: Path) -> dict[str, Any]:
    report = json.loads((trial_dir / LIVENESS_REPORT).read_text())
    (phase,) = report["phases"]
    return phase


@pytest.fixture(autouse=True)
def short_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS", "observe")
    monkeypatch.setenv("RAIDAR_LIVENESS_INTERVAL_S", "2")
    monkeypatch.setenv("RAIDAR_LIVENESS_IDLE_S", "20")
    monkeypatch.setenv("RAIDAR_DATA_CHECK", "0")


@pytest.mark.parametrize(
    ("mode", "decision"),
    [("hung", "idle_observed"), ("busy", "active"), ("chatty", "active")],
)
def test_observe_decides_idle_only_for_the_hung_agent(
    tmp_path: Path, mode: str, decision: str
) -> None:
    hold = 60 if mode == "hung" else 45
    trial_dir = asyncio.run(run_probe_trial(tmp_path, mode, hold, _context()))
    phase = _phase(trial_dir)
    print(mode, phase["decision"], phase["first_idle_at_s"], phase["unmeasured_samples"])
    assert phase["decision"] == decision
    assert phase["unmeasured_samples"] == 0 and phase["sample_count"] > 10
    measured = [s for s in phase["samples"] if s.get("quiet") is not None]
    if mode == "busy":
        assert any(s["cpu_ticks"] > 100 for s in measured)
    if mode == "chatty":
        assert any(s["output_chunks"] > 0 for s in measured)


def test_enforce_kills_a_hung_agent_and_a_detached_sleeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS", "enforce")
    trial_dir = asyncio.run(run_probe_trial(tmp_path, "hung_detached", 300, _context()))
    phase = _phase(trial_dir)
    assert phase["decision"] == "idle_killed", phase["reason"]
    assert phase["kill"]["residual"] == [] and phase["kill"]["error"] is None
    assert any(DETACHED_MARKER in line for line in phase["kill"]["killed"])
    assert phase["first_idle_at_s"] < 60  # not the 300 s hold
    after_path = trial_dir / "steps" / "checkpoint_1" / "agent" / AFTER_PHASE_REPORT
    after = json.loads(after_path.read_text())
    # What remains is root's container keep-alive (``sleep infinity``), nothing of uid 1000.
    leftovers = [line.split() for line in after["ps"].splitlines() if line.strip()]
    assert all(fields[1] == "0" for fields in leftovers), after["ps"]
    assert DETACHED_MARKER not in after["ps"]


class DockerExecEnv:
    """The slice of a Harbor environment the monitor uses, over ``docker exec``."""

    def __init__(self, context: str, container: str) -> None:
        self.context, self.container = context, container

    async def exec(self, command: str, user: str | None = None, **_: Any) -> Any:
        done = await asyncio.to_thread(
            subprocess.run,
            ["docker", "--context", self.context, "exec", "-u", user or "root", self.container]
            + ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if done.returncode != 0:
            raise RuntimeError(f"docker exec failed: {done.stderr.strip()[:200]}")
        return SimpleNamespace(stdout=done.stdout, stderr=done.stderr, return_code=0)


def test_a_stopped_container_is_unmeasured_not_idle() -> None:
    context, name = _context(), f"raidar-liveness-spike-{uuid.uuid4().hex[:8]}"
    image = image_tag(load_pack_spec())
    run = ["docker", "--context", context]
    subprocess.run([*run, "run", "-d", "--name", name, image, "sleep", "600"], check=True)
    try:
        monitor = PhaseMonitor(
            LivenessConfig("observe", idle_s=1), DockerExecEnv(context, name), "agent"
        )

        async def go() -> None:
            await monitor.sample()  # baseline from a live container
            await monitor.sample()
            subprocess.run([*run, "stop", "-t", "1", name], check=True, capture_output=True)
            await monitor.sample()

        asyncio.run(go())
        live, dead = list(monitor.samples)[1], monitor.samples[2]
        assert live["measured"] and not dead["measured"] and "unmeasured" in dead
        assert monitor.decision == "active"
    finally:
        subprocess.run([*run, "rm", "-f", name], capture_output=True)
