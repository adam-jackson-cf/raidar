"""The generated per-step reset ``setup.sh`` run with real bash against temp directories (F3)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from raidar.engine.health.setup_script import render_setup_script, reset_check

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def bash5() -> str:
    for path in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash", "/usr/bin/bash"):
        if (
            Path(path).exists()
            and subprocess.run([path, "-c", "((BASH_VERSINFO[0]>=4))"]).returncode == 0
        ):
            return path
    pytest.skip("needs bash >= 4 (associative arrays)")


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


def test_setup_script_resets_harness_state_to_the_baseline(tmp_path: Path, bash5: str) -> None:
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

    subprocess.run([bash5, str(script)], env=_env(tmp_path, RAIDAR_RESET_SKIP_KILL="1"), check=True)

    assert _tree(codex_home) == {"config.toml": "x = 1\n"}
    assert _tree(home / ".codex") == {"config.toml": "x = 1\n"}
    for gone in (home / ".agents", logs / "sessions", home / ".claude", home / ".claude.json"):
        assert not gone.exists()
    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert reset["skip_kill"] is True
    assert not any(entry["reset_failed"] for entry in reset["state"])
    assert reset_check(logs).passed
    assert not script.exists(), "setup.sh must delete itself from the workspace"


def test_root_agent_user_is_refused_not_killed_and_fails_the_reset_check(
    tmp_path: Path, bash5: str
) -> None:
    logs, workspace = tmp_path / "logs", tmp_path / "app"
    workspace.mkdir()
    script = workspace / "setup.sh"
    script.write_text(render_setup_script())

    subprocess.run([bash5, str(script)], env=_env(tmp_path, RAIDAR_RESET_KILL_UID="0"), check=True)

    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert "agent user is root" in reset["error"]
    assert (reset["killed"], reset["residual"]) == ([], [])
    outcome = reset_check(logs)
    assert not outcome.passed
    assert "reset refused" in outcome.detail


@pytest.mark.parametrize("stashed", [True, False])
def test_setup_script_puts_back_the_agents_own_setup_sh(
    tmp_path: Path, stashed: bool, bash5: str
) -> None:
    workspace, stash = tmp_path / "app", tmp_path / "stash.sh"
    workspace.mkdir()
    if stashed:
        stash.write_text("echo agent-bootstrap\n")
    script = workspace / "setup.sh"
    script.write_text(render_setup_script())

    subprocess.run(
        [bash5, str(script)],
        env=_env(tmp_path, RAIDAR_RESET_SKIP_KILL="1", RAIDAR_AGENT_SETUP_STASH=str(stash)),
        check=True,
    )

    if stashed:
        assert script.read_text() == "echo agent-bootstrap\n"
    else:
        assert not script.exists()
