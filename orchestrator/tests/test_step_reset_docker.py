"""The 0.3 context-isolation check as a real two-step Harbor trial on the Python pack image.

Step 1 plants an orphaned agent-user process and a token in every harness-state location; step 2
must find neither. A trial without the reset is the negative control that proves the check can
fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.engine.health.setup_script import reset_check
from tests.docker_trial_support import IsolationProbeAgent, build_task, run_trial_sync

pytestmark = pytest.mark.docker

PROBE_AGENT = "tests.docker_trial_support:IsolationProbeAgent"


def _isolation_trial(tmp_path: Path, *, with_reset: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    task = tmp_path / ("isolation" if with_reset else "isolation-no-reset")
    build_task(
        task,
        {"checkpoint_1": "step checkpoint_1", "checkpoint_2": "step checkpoint_2"},
        reset=with_reset,
    )
    IsolationProbeAgent.tokens.clear()
    trial = run_trial_sync(
        task, tmp_path / "trials", AgentConfig(import_path=PROBE_AGENT), task.name
    )
    steps = trial / "steps"
    plant = json.loads((steps / "checkpoint_1/agent/raidar-plant.json").read_text())
    probe = json.loads((steps / "checkpoint_2/agent/raidar-probe.json").read_text())
    return plant, probe


def test_context_isolation_between_steps_in_a_real_harbor_trial(tmp_path: Path) -> None:
    plant, probe = _isolation_trial(tmp_path, with_reset=True)
    steps = tmp_path / "trials" / "isolation" / "steps"
    reset = json.loads((steps / "checkpoint_2/agent/raidar-reset.json").read_text())
    # Step 2's report must not have been written into step 1's archived copy.
    first = json.loads((steps / "checkpoint_1/agent/raidar-reset.json").read_text())
    assert first["finished_ms"] < reset["started_ms"]

    assert "raidar-planted-sentinel-checkpoint_1" in plant["planted"]
    assert probe["planted_alive"] == []
    assert probe["token_hits"] == {plant["token"]: []}
    assert any("raidar-planted-sentinel" in entry["args"] for entry in reset["killed"])
    assert reset["residual"] == []
    assert reset_check(steps / "checkpoint_2" / "agent").passed


def test_isolation_check_detects_leaks_without_the_reset(tmp_path: Path) -> None:
    plant, probe = _isolation_trial(tmp_path, with_reset=False)

    assert any("raidar-planted-sentinel" in line for line in probe["planted_alive"])
    assert probe["token_hits"][plant["token"]], "harness-state tokens must survive without reset"
