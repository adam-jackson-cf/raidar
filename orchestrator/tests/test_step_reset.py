"""Per-step context reset as a generated setup.sh (F3).

- The wipe path runs on the host against temp dirs.
- The 0.3 context-isolation check runs as a real two-step Harbor trial on the
  Python pack image (OrbStack): step 1 plants an orphaned agent-user process and
  a token in every harness-state location; step 2 must find neither. A trial
  without the reset is the negative control that proves the check can fail.
"""

from __future__ import annotations

import json
import os
import subprocess
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
from tests.docker_trial_support import (
    IsolationProbeAgent,
    build_task,
    docker_target_ready,
    run_trial_sync,
)

PROBE_AGENT = "tests.docker_trial_support:IsolationProbeAgent"
BASH5 = next(
    (
        path
        for path in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash", "/usr/bin/bash")
        if Path(path).exists()
        and subprocess.run([path, "-c", "((BASH_VERSINFO[0]>=4))"]).returncode == 0
    ),
    None,
)
needs_bash5 = pytest.mark.skipif(BASH5 is None, reason="needs bash >= 4 (associative arrays)")


def _tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file()}


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "RAIDAR_CODEX_HOME": str(tmp_path / "codex-home"),
        "RAIDAR_LOGS_DIR": str(tmp_path / "logs"),
        "RAIDAR_HARNESS_BASELINE_DIR": str(tmp_path / "baseline"),
        **extra,
    }


@needs_bash5
def test_setup_script_resets_harness_state_to_the_baseline(tmp_path: Path) -> None:
    home, logs, workspace = tmp_path / "home", tmp_path / "logs", tmp_path / "app"
    codex_home, baseline = tmp_path / "codex-home", tmp_path / "baseline"
    for path, text in {
        codex_home / "sessions/rollout-a.jsonl": "STEP1",
        home / ".codex/memories/memory.md": "STEP1",
        home / ".agents/skills/x/SKILL.md": "STEP1",
        logs / "sessions/projects/-app/s.jsonl": "STEP1",
        home / ".claude/projects/-app/x.jsonl": "STEP1",
        home / ".claude.json": "{}",
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (baseline / "codex-home").mkdir(parents=True)
    (baseline / "codex-home/config.toml").write_text("x = 1\n")
    workspace.mkdir()
    script = workspace / "setup.sh"
    script.write_text(render_setup_script())

    subprocess.run(
        [str(BASH5), str(script)], env=_env(tmp_path, RAIDAR_RESET_SKIP_KILL="1"), check=True
    )

    assert _tree(codex_home) == {"config.toml": "x = 1\n"}
    assert _tree(home / ".codex") == {"config.toml": "x = 1\n"}
    for gone in (home / ".agents", logs / "sessions", home / ".claude", home / ".claude.json"):
        assert not gone.exists()
    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert reset["skip_kill"] is True
    assert not any(entry["reset_failed"] for entry in reset["state"])
    assert reset_check(logs).passed
    assert not script.exists(), "setup.sh must delete itself from the workspace"


@needs_bash5
def test_root_agent_user_is_refused_not_killed_and_fails_the_reset_check(tmp_path: Path) -> None:
    logs, workspace = tmp_path / "logs", tmp_path / "app"
    workspace.mkdir()
    script = workspace / "setup.sh"
    script.write_text(render_setup_script())

    subprocess.run(
        [str(BASH5), str(script)], env=_env(tmp_path, RAIDAR_RESET_KILL_UID="0"), check=True
    )

    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert "agent user is root" in reset["error"]
    assert (reset["killed"], reset["residual"]) == ([], [])
    outcome = reset_check(logs)
    assert not outcome.passed
    assert "reset refused" in outcome.detail


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


docker_trial = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


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


@docker_trial
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


@docker_trial
def test_isolation_check_detects_leaks_without_the_reset(tmp_path: Path) -> None:
    plant, probe = _isolation_trial(tmp_path, with_reset=False)

    assert any("raidar-planted-sentinel" in line for line in probe["planted_alive"])
    assert probe["token_hits"][plant["token"]], "harness-state tokens must survive without reset"


@needs_bash5
@pytest.mark.parametrize("stashed", [True, False])
def test_setup_script_puts_back_the_agents_own_setup_sh(tmp_path: Path, stashed: bool) -> None:
    workspace, stash = tmp_path / "app", tmp_path / "stash.sh"
    workspace.mkdir()
    if stashed:
        stash.write_text("echo agent-bootstrap\n")
    script = workspace / "setup.sh"
    script.write_text(render_setup_script())

    subprocess.run(
        [str(BASH5), str(script)],
        env=_env(tmp_path, RAIDAR_RESET_SKIP_KILL="1", RAIDAR_AGENT_SETUP_STASH=str(stash)),
        check=True,
    )

    if stashed:
        assert script.read_text() == "echo agent-bootstrap\n"
    else:
        assert not script.exists()
