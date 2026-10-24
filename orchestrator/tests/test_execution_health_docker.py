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

from raidar.components.runtime.provision import condition_provisions, install_trial_provisioning
from raidar.execution.cleanup import cleanup_trial_projects
from raidar.execution.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, SCOPE_KWARG, run_scope
from raidar.execution.health.signals import tool_status
from raidar.execution.health.tool_tree import ToolTreeWatch, load_tool_hashes
from raidar.targets.config import activate
from tests.component_support import PROBE_DIR, compile_uses, use
from tests.component_trial_support import build_task, docker_target, docker_target_ready

TOOL = "/opt/raidar-tools/01-lint-tool/ruff-0.8.6/bin/ruff"
STEPS = ("checkpoint_1", "checkpoint_2")

pytestmark = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


async def _run(tmp_path: Path, tamper_on_step: int | None) -> Path:
    compiled = compile_uses(
        use("lint-config@1", (f"{PROBE_DIR}/ruff.toml",)), use("lint-tool@2", config="ruff.toml")
    )
    task = tmp_path / "task"
    build_task(task, compiled, {step: step for step in STEPS})
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
        install_trial_provisioning(trial, condition_provisions(compiled))
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
