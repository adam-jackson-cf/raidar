"""T1 against a real two-step Harbor trial: a root-side change to the tool tree is a violation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.trial import Trial

from raidar.engine.health.signals import tool_status
from raidar.engine.health.tool_tree import ToolTreeWatch, load_tool_hashes
from raidar.engine.target.config import activate
from raidar.engine.trial.cleanup import cleanup_trial_projects
from raidar.engine.trial.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, SCOPE_KWARG, run_scope
from raidar.engine.trial.provision import PayloadFile, TrialProvision, install_trial_provisioning
from tests.docker_trial_support import build_task, docker_target, docker_target_ready

TOOL = "/opt/raidar-tools/probe-tool/bin/probe"
STEPS = ("checkpoint_1", "checkpoint_2")
# A root-owned tool in the provisioned tree, the shape every lab provision installs.
PROBE_PROVISION = TrialProvision(
    slug="probe-tool",
    payload={"probe": PayloadFile(b"#!/bin/sh\necho probe\n", executable=True)},
    install=(
        'mkdir -p /opt/raidar-tools/probe-tool/bin && cp "$RAIDAR_PAYLOAD_DIR/probe" '
        f"{TOOL} && chmod 755 {TOOL}"
    ),
    check=f"test -x {TOOL}",
)

pytestmark = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


async def _run(tmp_path: Path, tamper_on_step: int | None) -> Path:
    task = tmp_path / "task"
    build_task(task, {step: step for step in STEPS}, reset=False)
    activate(docker_target())
    trials_dir = tmp_path / "trials"
    config = TrialConfig(
        task=TaskConfig(path=task),
        trial_name="t1-tamper",
        trials_dir=trials_dir,
        agent=AgentConfig(name="nop"),
        environment=EnvironmentConfig(
            type="docker",
            import_path=RUN_SCOPED_DOCKER_IMPORT_PATH,
            delete=True,
            kwargs={SCOPE_KWARG: run_scope(trials_dir)},
        ),
        verifier=VerifierConfig(disable=True),
    )
    try:
        trial = await Trial.create(config)
        install_trial_provisioning(trial, [PROBE_PROVISION])
        watch = ToolTreeWatch(trial, SimpleNamespace(resolve_step=lambda name: name))  # type: ignore[arg-type]
        ends = 0

        async def tamper(event: TrialHookEvent) -> None:
            nonlocal ends
            ends += 1
            if ends - 1 == tamper_on_step:  # root edits the tool, before the post-step hash
                command = f"echo tampered >> {TOOL}"
                await trial.agent_environment.exec(command, user="root", timeout_sec=60)

        trial.add_hook(TrialEvent.AGENT_START, watch.before_agent)
        trial.add_hook(TrialEvent.AGENT_END, tamper)
        trial.add_hook(TrialEvent.AGENT_END, watch.after_agent)
        await trial.run()
    finally:
        cleanup_trial_projects(tmp_path, docker_context=docker_target().docker_context)
    return trials_dir / "t1-tamper"


def _status(trial_dir: Path) -> tuple[str, str, dict[str, Any]]:
    recorded = load_tool_hashes(trial_dir)
    assert recorded is not None
    return tool_status(recorded["points"])


def test_an_untouched_tool_tree_hashes_the_same_across_steps(tmp_path: Path) -> None:
    import asyncio

    status, detail, _ = _status(asyncio.run(_run(tmp_path, None)))
    assert status == "pass", detail


def test_a_root_edit_to_the_provisioned_tool_is_a_t1_violation(tmp_path: Path) -> None:
    import asyncio

    trial_dir = asyncio.run(_run(tmp_path, 1))
    points = load_tool_hashes(trial_dir)["points"]  # type: ignore[index]
    assert [p["point"] for p in points] == ["baseline", "after_agent", "after_agent"]
    assert points[0]["sha256"] == points[1]["sha256"] != points[2]["sha256"]
    status, detail, evidence = _status(trial_dir)
    assert status == "violation", detail
    assert evidence["steps"] == ["checkpoint_2"]
