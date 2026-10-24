"""Per-step context reset as a generated setup.sh (F3).

- The wipe/materialise/evidence path runs on the host against temp dirs.
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

import pytest
from harbor.models.trial.config import AgentConfig
from harbor.trial.trial import Trial

from raidar.components import compile_condition
from raidar.components.definition.contribution import SupportFile
from raidar.components.runtime.setup_script import render_setup_script
from raidar.execution.environment import RUN_SCOPED_DOCKER_IMPORT_PATH, run_scope
from tests.component_support import PILOT_DIR, agent, all_builtin_uses, compile_uses, condition, use
from tests.component_trial_support import (
    IsolationProbeAgent,
    build_task,
    docker_target_ready,
    run_trial_sync,
)

BASH5 = next(
    (
        path
        for path in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash", "/usr/bin/bash")
        if Path(path).exists()
        and subprocess.run([path, "-c", "((BASH_VERSINFO[0]>=4))"]).returncode == 0
    ),
    None,
)


def _tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file()}


@pytest.mark.skipif(BASH5 is None, reason="needs bash >= 4 (associative arrays)")
def test_setup_script_resets_state_materialises_support_and_records_workspace(
    tmp_path: Path,
) -> None:
    home, logs, workspace = tmp_path / "home", tmp_path / "logs", tmp_path / "app"
    codex_home, baseline = tmp_path / "codex-home", tmp_path / "baseline"
    components, workflow = tmp_path / "components", tmp_path / "review"
    for path, text in {
        codex_home / "sessions/rollout-a.jsonl": "STEP1",
        home / ".codex/memories/memory.md": "STEP1",
        home / ".agents/skills/x/SKILL.md": "STEP1",
        logs / "sessions/projects/-app/s.jsonl": "STEP1",
        home / ".claude/projects/-app/x.jsonl": "STEP1",
        home / ".claude.json": "{}",
        workflow / "reasoning.md": "STEP1",
        components / "stale/file": "STEP1",
        workspace / "AGENTS.md": "# rules\n",
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (baseline / "codex-home").mkdir(parents=True)
    (baseline / "codex-home/config.toml").write_text("x = 1\n")
    script = workspace / "setup.sh"
    script.write_text(
        render_setup_script(
            support_files={"00-hook/hook.sh": SupportFile(b"echo hi\n", executable=True)},
            workspace_paths=["AGENTS.md", "docs/missing.txt"],
            component_slugs=["00-hook"],
        )
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "RAIDAR_CODEX_HOME": str(codex_home),
        "RAIDAR_LOGS_DIR": str(logs),
        "RAIDAR_COMPONENT_ROOT": str(components),
        "RAIDAR_WORKFLOW_DIR": str(workflow),
        "RAIDAR_HARNESS_BASELINE_DIR": str(baseline),
        "RAIDAR_RESET_SKIP_KILL": "1",
    }

    subprocess.run([str(BASH5), str(script)], env=env, check=True)

    assert _tree(codex_home) == {"config.toml": "x = 1\n"}
    assert _tree(home / ".codex") == {"config.toml": "x = 1\n"}
    for gone in (home / ".agents", logs / "sessions", home / ".claude", home / ".claude.json"):
        assert not gone.exists()
    assert not workflow.exists()
    assert _tree(components) == {"00-hook/hook.sh": "echo hi\n"}
    assert os.access(components / "00-hook/hook.sh", os.X_OK)
    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert reset["skip_kill"] is True
    assert not any(entry["reset_failed"] for entry in reset["state"])
    recorded = json.loads((logs / "raidar-components.json").read_text())
    assert recorded["workspace_files"]["docs/missing.txt"] is None
    assert len(recorded["workspace_files"]["AGENTS.md"]) == 64
    assert recorded["components"] == ["00-hook"]
    assert not script.exists(), "setup.sh must delete itself from the workspace"


@pytest.mark.skipif(BASH5 is None, reason="needs bash >= 4 (associative arrays)")
def test_root_agent_user_is_refused_not_killed_and_fails_the_reset_probe(tmp_path: Path) -> None:
    from raidar.components.definition.compiled import reset_probe
    from raidar.components.definition.evidence import StepEvidence

    logs, workspace = tmp_path / "logs", tmp_path / "app"
    workspace.mkdir()
    script = workspace / "setup.sh"
    script.write_text(render_setup_script(support_files={}, workspace_paths=[], component_slugs=[]))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "RAIDAR_CODEX_HOME": str(tmp_path / "codex-home"),
        "RAIDAR_LOGS_DIR": str(logs),
        "RAIDAR_COMPONENT_ROOT": str(tmp_path / "components"),
        "RAIDAR_WORKFLOW_DIR": str(tmp_path / "review"),
        "RAIDAR_HARNESS_BASELINE_DIR": str(tmp_path / "baseline"),
        "RAIDAR_RESET_KILL_UID": "0",
    }

    subprocess.run([str(BASH5), str(script)], env=env, check=True)

    reset = json.loads((logs / "raidar-reset.json").read_text())
    assert "agent user is root" in reset["error"]
    assert (reset["killed"], reset["residual"]) == ([], [])
    outcome = reset_probe(StepEvidence(logs))
    assert not outcome.passed
    assert "reset refused" in outcome.detail


def test_setup_script_refuses_paths_that_could_escape_quoting() -> None:
    for bad in ("../x", "a b", 'x"y', "/abs"):
        with pytest.raises(ValueError, match="unsafe"):
            render_setup_script(support_files={}, workspace_paths=[bad], component_slugs=[])
        with pytest.raises(ValueError, match="unsafe"):
            render_setup_script(
                support_files={bad: SupportFile(b"")}, workspace_paths=[], component_slugs=[]
            )


def test_every_step_gets_the_reset_and_only_the_first_gets_the_overlay(tmp_path: Path) -> None:
    compiled = compile_uses(*all_builtin_uses(agent().harness))
    compiled.write_task_steps(tmp_path, ["checkpoint_1", "checkpoint_2"])
    first, second = (tmp_path / "steps" / s / "workdir" for s in ("checkpoint_1", "checkpoint_2"))

    assert sorted(p.relative_to(first).as_posix() for p in first.rglob("*") if p.is_file()) == [
        "AGENTS.md",
        "docs/starter/NOTES.txt",
        "ruff.toml",
        "setup.sh",
    ]
    assert [p.name for p in second.iterdir()] == ["setup.sh"]
    assert (first / "setup.sh").read_text() == (second / "setup.sh").read_text()
    assert os.access(first / "setup.sh", os.X_OK)
    with pytest.raises(FileExistsError):
        compiled.write_task_steps(tmp_path, ["checkpoint_1"])
    with pytest.raises(ValueError, match="at least one step"):
        compiled.write_task_steps(tmp_path, [])
    baseline = compile_condition(condition(), agent(), base_dir=PILOT_DIR)
    assert "kill -KILL" in baseline.setup_script()
    assert compile_uses(use("stop-hook@1", command="true")).missing_secrets({}) == []


def test_component_trials_get_a_per_run_compose_project_and_always_clean_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list = []
    cleaned: list[Path] = []

    async def failing_create(config: object) -> None:
        seen.append(config)
        raise RuntimeError("compose up failed")

    monkeypatch.setattr(Trial, "create", staticmethod(failing_create))
    monkeypatch.setattr(
        "tests.component_trial_support.cleanup_trial_projects",
        lambda job_dir, **_: cleaned.append(job_dir),
    )
    monkeypatch.setenv("RAIDAR_TARGETS_FILE", str(tmp_path / "no-targets.toml"))
    monkeypatch.delenv("RAIDAR_TARGET", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "test-ctx")
    agent_config = AgentConfig(import_path="tests.component_trial_support:IsolationProbeAgent")
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
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


def _isolation_trial(tmp_path: Path, *, with_reset: bool) -> tuple[dict, dict]:
    compiled = compile_condition(condition(), agent(), base_dir=PILOT_DIR) if with_reset else None
    task = tmp_path / ("isolation" if with_reset else "isolation-no-reset")
    build_task(
        task, compiled, {"checkpoint_1": "step checkpoint_1", "checkpoint_2": "step checkpoint_2"}
    )
    IsolationProbeAgent.tokens.clear()
    trial = run_trial_sync(
        task,
        tmp_path / "trials",
        AgentConfig(import_path="tests.component_trial_support:IsolationProbeAgent"),
        task.name,
    )
    steps = trial / "steps"
    plant = json.loads((steps / "checkpoint_1/agent/raidar-plant.json").read_text())
    probe = json.loads((steps / "checkpoint_2/agent/raidar-probe.json").read_text())
    return plant, probe


@docker_trial
def test_context_isolation_between_steps_in_a_real_harbor_trial(tmp_path: Path) -> None:
    plant, probe = _isolation_trial(tmp_path, with_reset=True)
    trial = tmp_path / "trials" / "isolation" / "steps"
    reset = json.loads((trial / "checkpoint_2/agent/raidar-reset.json").read_text())
    # Step 2's report must not have been written into step 1's archived copy.
    first = json.loads((trial / "checkpoint_1/agent/raidar-reset.json").read_text())
    assert first["finished_ms"] < reset["started_ms"]

    assert "raidar-planted-sentinel-checkpoint_1" in plant["planted"]
    assert probe["planted_alive"] == []
    assert probe["token_hits"] == {plant["token"]: []}
    assert any("raidar-planted-sentinel" in entry["args"] for entry in reset["killed"])
    assert reset["residual"] == []
    compiled = compile_condition(condition(), agent(), base_dir=PILOT_DIR)
    assert compiled.probe_step(trial / "checkpoint_2" / "agent", 1).passed


@docker_trial
def test_isolation_check_detects_leaks_without_the_reset(tmp_path: Path) -> None:
    plant, probe = _isolation_trial(tmp_path, with_reset=False)

    assert any("raidar-planted-sentinel" in line for line in probe["planted_alive"])
    assert probe["token_hits"][plant["token"]], "harness-state tokens must survive without reset"


@pytest.mark.skipif(BASH5 is None, reason="needs bash >= 4 (associative arrays)")
@pytest.mark.parametrize("stashed", [True, False])
def test_setup_script_puts_back_the_agents_own_setup_sh(tmp_path: Path, stashed: bool) -> None:
    workspace, stash = tmp_path / "app", tmp_path / "stash.sh"
    workspace.mkdir()
    if stashed:
        stash.write_text("echo agent-bootstrap\n")
    script = workspace / "setup.sh"
    script.write_text(render_setup_script(support_files={}, workspace_paths=[], component_slugs=[]))
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "RAIDAR_CODEX_HOME": str(tmp_path / "codex-home"),
        "RAIDAR_LOGS_DIR": str(tmp_path / "logs"),
        "RAIDAR_COMPONENT_ROOT": str(tmp_path / "components"),
        "RAIDAR_WORKFLOW_DIR": str(tmp_path / "review"),
        "RAIDAR_HARNESS_BASELINE_DIR": str(tmp_path / "baseline"),
        "RAIDAR_RESET_SKIP_KILL": "1",
        "RAIDAR_AGENT_SETUP_STASH": str(stash),
    }

    subprocess.run([str(BASH5), str(script)], env=env, check=True)

    if stashed:
        assert script.read_text() == "echo agent-bootstrap\n"
    else:
        assert not script.exists()
