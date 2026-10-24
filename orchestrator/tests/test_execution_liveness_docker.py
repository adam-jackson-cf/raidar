"""Liveness monitor against real Harbor trials (opt in with ``RAIDAR_EXECUTION_DOCKER=1``).

The Docker target is the current one (``TARGET=<name>`` / ``RAIDAR_TARGET``, else the targets
file's default). Thresholds are short so each trial lasts about a minute.
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

from raidar.engine.health.liveness import (
    LIVENESS_REPORT,
    LivenessConfig,
    PhaseMonitor,
    idle_killed_steps,
)
from raidar.engine.health.tool_tree import hash_tool_tree
from raidar.engine.trial.collect import _infra_reasons
from raidar.engine.trial.models import ProbeSummary
from raidar.subject.environment.python.image import image_tag
from raidar.subject.environment.python.models import load_pack_spec
from tests.component_trial_support import docker_context
from tests.liveness_support import AFTER_PHASE_REPORT, DETACHED_MARKER, run_probe_trial

pytestmark = pytest.mark.skipif(
    os.environ.get("RAIDAR_EXECUTION_DOCKER") != "1",
    reason="set RAIDAR_EXECUTION_DOCKER=1 (and TARGET=<name>) to run real trials",
)


def _context() -> str:
    return docker_context()


def _phase(trial_dir: Path) -> dict[str, Any]:
    report = json.loads((trial_dir / LIVENESS_REPORT).read_text())
    (phase,) = report["phases"]
    return phase


@pytest.fixture(autouse=True)
def short_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS", "observe")
    monkeypatch.setenv("RAIDAR_LIVENESS_INTERVAL_S", "2")
    monkeypatch.setenv("RAIDAR_LIVENESS_IDLE_S", "20")


@pytest.mark.parametrize(
    ("mode", "decision"),
    [("hung", "idle_observed"), ("busy", "active"), ("chatty", "active")],
)
def test_observe_decides_idle_only_for_the_hung_agent(
    tmp_path: Path, mode: str, decision: str
) -> None:
    hold = 60 if mode == "hung" else 45
    trial_dir = asyncio.run(run_probe_trial(tmp_path, mode, hold))
    phase = _phase(trial_dir)
    print(mode, phase["decision"], phase["first_idle_at_s"], phase["unmeasured_samples"])
    assert phase["decision"] == decision
    assert phase["unmeasured_samples"] == 0
    assert phase["sample_count"] > 10
    measured = [s for s in phase["samples"] if s.get("quiet") is not None]
    if mode == "busy":
        assert any(s["cpu_ticks"] > 100 for s in measured)
    if mode == "chatty":
        assert any(s["output_chunks"] > 0 for s in measured)


def test_enforce_kills_a_hung_agent_and_a_detached_sleeper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIDAR_LIVENESS", "enforce")
    trial_dir = asyncio.run(run_probe_trial(tmp_path, "hung_detached", 300))
    phase = _phase(trial_dir)
    assert phase["decision"] == "idle_killed", phase["reason"]
    assert phase["kill"]["residual"] == []
    assert phase["kill"]["error"] is None
    assert any(DETACHED_MARKER in line for line in phase["kill"]["killed"])
    assert phase["first_idle_at_s"] < 60  # not the 300 s hold
    after_path = trial_dir / "steps" / "checkpoint_1" / "agent" / AFTER_PHASE_REPORT
    after = json.loads(after_path.read_text())
    # What remains is root's container keep-alive (``sleep infinity``), nothing of uid 1000.
    leftovers = [line.split() for line in after["ps"].splitlines() if line.strip()]
    assert all(fields[1] == "0" for fields in leftovers), after["ps"]
    assert DETACHED_MARKER not in after["ps"]


def test_an_enforce_kill_ends_the_step_like_a_timeout_and_the_trial_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Harbor records the kill as a non-zero agent exit, runs the verifier and the next step."""
    monkeypatch.setenv("RAIDAR_LIVENESS", "enforce")
    trial_dir = asyncio.run(run_probe_trial(tmp_path, "hung", 300, steps=2, verify=True))
    report = json.loads((trial_dir / LIVENESS_REPORT).read_text())
    assert [p["decision"] for p in report["phases"]] == ["idle_killed", "idle_killed"]
    assert [p["step"] for p in report["phases"]] == ["checkpoint_1", "checkpoint_2"]
    killed = idle_killed_steps(trial_dir)
    assert killed == {"checkpoint_1", "checkpoint_2"}
    result = json.loads((trial_dir / "result.json").read_text())
    assert result.get("exception_info") is None  # the trial itself did not fail
    probes = ProbeSummary(passed=True, failures=())
    for item in result["step_results"]:
        kind = item["exception_info"]["exception_type"]
        assert kind == "NonZeroAgentExitCodeError"
        assert item["verifier_result"]["rewards"]["reward"] == 1  # verified despite the kill
        reasons = _infra_reasons(None, kind, probes, None, item["step_name"] in killed)
        assert not [r for r in reasons if r.startswith("step_exception")]


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
    context, name = _context(), f"raidar-liveness-{uuid.uuid4().hex[:8]}"
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
        assert live["measured"]
        assert not dead["measured"]
        assert "unmeasured" in dead
        assert monitor.verdict.decision == "active"
    finally:
        subprocess.run([*run, "rm", "-f", name], capture_output=True)


def test_the_tool_tree_hash_sees_content_mode_links_and_ignores_staging() -> None:
    context, name = _context(), f"raidar-tooltree-{uuid.uuid4().hex[:8]}"
    run = ["docker", "--context", context]
    image = image_tag(load_pack_spec())
    subprocess.run([*run, "run", "-d", "--name", name, image, "sleep", "600"], check=True)
    env = DockerExecEnv(context, name)

    def sh(command: str) -> None:
        subprocess.run([*run, "exec", "-u", "root", name, "bash", "-c", command], check=True)

    async def digest() -> tuple[bool, str | None]:
        reading, error = await hash_tool_tree(env)
        assert reading is not None, error
        return reading.present, reading.sha256

    try:
        assert asyncio.run(digest()) == (False, None)
        sh("mkdir -p /opt/raidar-tools/tool/bin /opt/raidar-tools/.staging")
        sh("echo a > /opt/raidar-tools/tool/bin/t")
        sh("ln -s /opt/raidar-tools/tool/bin/t /usr/local/bin/raidar-test-link")
        baseline = asyncio.run(digest())
        assert baseline[0] is True
        assert asyncio.run(digest()) == baseline
        sh("echo scratch > /opt/raidar-tools/.staging/x")  # the install's scratch area
        assert asyncio.run(digest()) == baseline
        for tamper in (
            "echo b > /opt/raidar-tools/tool/bin/t",  # content
            "chmod 777 /opt/raidar-tools/tool/bin/t",  # mode
            "rm /usr/local/bin/raidar-test-link && ln -s /bin/sh /usr/local/bin/raidar-test-link",
            "echo c > /opt/raidar-tools/tool/new",  # an added file
        ):
            sh(tamper)
            changed = asyncio.run(digest())
            assert changed != baseline, tamper
            baseline = changed
    finally:
        subprocess.run([*run, "rm", "-f", name], capture_output=True)
