"""Per-step reset scripts and the per-run compose project of trials (F3).

Real bash runs live in ``test_step_reset_script.py`` and real Harbor trials in
``test_step_reset_docker.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig
from harbor.trial.trial import Trial

from raidar.engine.health.setup_script import (
    render_setup_script,
    reset_check,
    write_setup_scripts,
)
from raidar.engine.trial.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, run_scope
from tests.docker_trial_support import run_trial_sync

PROBE_AGENT = "tests.docker_trial_support:IsolationProbeAgent"


def test_reset_check_fails_when_the_reset_never_ran(tmp_path: Path) -> None:
    outcome = reset_check(tmp_path)

    assert not outcome.passed
    assert "setup.sh did not run" in outcome.detail


def test_every_step_gets_the_same_executable_reset(tmp_path: Path) -> None:
    write_setup_scripts(tmp_path, ["checkpoint_1", "checkpoint_2"])
    first, second = (
        tmp_path / "steps" / step / "workdir" / "setup.sh"
        for step in ("checkpoint_1", "checkpoint_2")
    )

    assert first.read_text() == second.read_text() == render_setup_script()
    assert "kill -KILL" in first.read_text()
    assert os.access(first, os.X_OK)
    with pytest.raises(FileExistsError):
        write_setup_scripts(tmp_path, ["checkpoint_1"])
    with pytest.raises(ValueError, match="at least one step"):
        write_setup_scripts(tmp_path / "empty", [])


def test_trials_get_a_per_run_compose_project_and_always_clean_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    cleaned: list[Path] = []

    async def failing_create(config: object) -> None:
        seen.append(config)
        raise RuntimeError("compose up failed")

    monkeypatch.setattr(Trial, "create", staticmethod(failing_create))
    monkeypatch.setattr(
        "tests.docker_trial_support.cleanup_trial_projects",
        lambda job_dir, **_: cleaned.append(job_dir),
    )
    targets = tmp_path / "targets.toml"
    targets.write_text('default = "offline"\n[targets.offline]\ndocker_context = "test-ctx"\n')
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(targets))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    agent_config = AgentConfig(import_path=PROBE_AGENT)
    with pytest.raises(RuntimeError, match="compose up failed"):
        run_trial_sync(tmp_path / "task", tmp_path / "trials", agent_config, "isolation")

    environment = seen[0].environment
    assert environment.import_path == RUN_SCOPED_DOCKER_IMPORT_PATH
    assert environment.delete is True
    assert environment.kwargs == {"run_scope": run_scope(tmp_path / "trials")}
    assert cleaned == [tmp_path]
    with pytest.raises(ValueError, match="named 'trials'"):
        run_trial_sync(tmp_path / "task", tmp_path / "other", agent_config, "isolation")
