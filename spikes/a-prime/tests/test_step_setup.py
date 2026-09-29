"""Unit checks for the per-step reset script (raidar_spike/step_setup.sh).

* wipe path (CODEX_HOME and CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude.json) runs
  on the host against temp dirs with the kill phase disabled;
* kill path runs inside a throwaway OrbStack container as uid 1000, because it
  kills every process of the invoking uid.

Run: ../../orchestrator/.venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "raidar_spike" / "step_setup.sh"
BASH5 = next(
    (
        b
        for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash")
        if Path(b).exists() and subprocess.run([b, "-c", "((BASH_VERSINFO[0]>=4))"]).returncode == 0
    ),
    None,
)


def _tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file()}


@pytest.mark.skipif(BASH5 is None, reason="needs bash >= 4 (associative arrays)")
def test_wipe_resets_codex_and_claude_state_to_baseline(tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    claude_dir = tmp_path / "claude-config"
    baseline = tmp_path / "baseline"
    # Step-1 leftovers: sessions, memory, history, transcripts.
    for path, text in {
        codex_home / "sessions/2026/09/rollout-abc.jsonl": "STEP1-TRANSCRIPT",
        codex_home / "history.jsonl": "STEP1-HISTORY",
        home / ".codex/memories/memory.md": "STEP1-MEMORY",
        claude_dir / "projects/-app/session.jsonl": "STEP1-CLAUDE",
        claude_dir / "todos/t.json": "STEP1-TODO",
        home / ".claude/projects/-app/x.jsonl": "STEP1-CLAUDE-HOME",
        home / ".claude.json": '{"projects":{"/app":{"history":["STEP1"]}}}',
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (baseline / "codex-home").mkdir(parents=True)
    (baseline / "codex-home/config.toml").write_text("model_reasoning_effort = 'low'\n")
    (baseline / "claude-config").mkdir(parents=True)
    (baseline / "claude-config/settings.json").write_text('{"condition":"baseline"}')
    script = tmp_path / "workdir/setup.sh"
    script.parent.mkdir()
    shutil.copy2(SCRIPT, script)
    report = tmp_path / "report.json"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "RAIDAR_CODEX_HOME": str(codex_home),
        "CLAUDE_CONFIG_DIR": str(claude_dir),
        "RAIDAR_HARNESS_BASELINE_DIR": str(baseline),
        "RAIDAR_RESET_REPORT": str(report),
        "RAIDAR_RESET_SKIP_KILL": "1",
    }
    subprocess.run([BASH5, str(script)], env=env, check=True)

    assert _tree(codex_home) == {"config.toml": "model_reasoning_effort = 'low'\n"}
    assert _tree(home / ".codex") == {"config.toml": "model_reasoning_effort = 'low'\n"}
    assert _tree(claude_dir) == {"settings.json": '{"condition":"baseline"}'}
    assert _tree(home / ".claude") == {"settings.json": '{"condition":"baseline"}'}
    assert not (home / ".claude.json").exists()  # no baseline -> removed
    payload = json.loads(report.read_text())
    assert payload["skip_kill"] is True
    assert all(not entry["reset_failed"] for entry in payload["state"])
    assert not script.exists(), "setup.sh must delete itself from the workspace"


def _oracle_image() -> str | None:
    out = subprocess.run(
        ["docker", "--context", "orbstack", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    )
    return next(
        (line for line in out.stdout.splitlines() if line.startswith("raidar-spike/")), None
    )


@pytest.mark.skipif(_oracle_image() is None, reason="needs a built raidar-spike image on orbstack")
def test_kill_removes_leftover_agent_processes_only() -> None:
    image = _oracle_image()
    inner = r"""
set -e
# Step-1 leftovers owned by the agent user (uid 1000), incl. an orphaned setsid one.
runuser -u agent -- bash -c "nohup setsid bash -c 'exec -a raidar-planted-sentinel-unit sleep 3000' >/dev/null 2>&1 &"
runuser -u agent -- bash -c "nohup sleep 3001 >/dev/null 2>&1 &"
# A root process that must survive (not agent-owned).
nohup bash -c 'exec -a root-survivor sleep 3002' >/dev/null 2>&1 &
sleep 0.5
cp /spike/step_setup.sh /tmp/setup.sh && chmod 755 /tmp/setup.sh
runuser -u agent -- env HOME=/tmp/agent_home RAIDAR_RESET_REPORT=/tmp/report.json bash /tmp/setup.sh
echo "---REPORT---"; cat /tmp/report.json; echo
echo "---PS---"; ps -e -o pid=,uid=,args=
"""
    proc = subprocess.run(
        [
            "docker",
            "--context",
            "orbstack",
            "run",
            "--rm",
            "-v",
            f"{SCRIPT.parent}:/spike:ro",
            image,
            "bash",
            "-c",
            inner,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report_text = proc.stdout.split("---REPORT---")[1].split("---PS---")[0].strip()
    ps_after = proc.stdout.split("---PS---")[1]
    report = json.loads(report_text)
    killed = " ".join(k["args"] for k in report["killed"])
    assert "raidar-planted-sentinel-unit" in killed
    assert "sleep 3001" in killed
    assert report["residual"] == []
    assert "raidar-planted-sentinel-unit" not in ps_after
    assert "sleep 3001" not in ps_after
    assert "root-survivor" in ps_after
