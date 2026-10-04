"""guardrail-gate@2: guardrail-gate@1 plus a shadow mode and git evidence of every commit.

The same scripted inner agent and local-shell environment as guardrail-gate@1's
tests run the gate's real shell script (git, the pre-commit hook, ``git
commit``). Shadow mode must record the same commit check without writing the
workspace or starting a fix session; every commit attempt records ``HEAD``
before and after, ``git log --oneline`` and ``git status --porcelain``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from raidar.components import ComponentRegistry, ComponentRejectedError, StepEvidence
from raidar.components.builtins.gate import GUARDRAIL_GATE, gate_signal
from raidar.components.builtins.gate_v2 import GUARDRAIL_GATE_V2, GUARDRAIL_GATE_V2_IMPORT_PATH
from raidar.components.gate_run import GATE_RECORD, SIGNAL
from raidar.components.gate_run_v2 import (
    GIT_LOG_MARK,
    GIT_STATUS_MARK,
    GateModeV2,
    read_gate_run,
)
from raidar.components.guardrail_gate_v2 import GuardrailGateV2Agent
from raidar.components.registry import fingerprint
from tests.component_support import compile_uses, use
from tests.test_guardrail_gate_workflow import (
    INNER,
    TOY_GATE,
    LocalEnvironment,
    ScriptedInnerAgent,
)
from tests.test_guardrail_gate_workflow import _reset_script as _reset_script  # noqa: F401

REF = "guardrail-gate@2"


@pytest.fixture
def shims(tmp_path: Path) -> Path:
    """A pass-through ``timeout`` where the host has none (macOS), as for guardrail-gate@1."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        shim = bin_dir / "timeout"
        shim.write_text('#!/bin/sh\n[ "$1" = "-k" ] && shift 2\nshift\nexec "$@"\n')
        shim.chmod(0o755)
    return bin_dir


def _agent(
    tmp_path: Path, shims_dir: Path, **options: Any
) -> tuple[GuardrailGateV2Agent, LocalEnvironment]:
    workspace, logs = tmp_path / "app", tmp_path / "logs"
    workspace.mkdir(parents=True)
    logs.mkdir()
    (workspace / "main.py").write_text("print('hi')\n")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "big.bin").write_text("not for the repository\n")
    (logs / "raidar-reset.json").write_text("{}")
    agent = GuardrailGateV2Agent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=logs,  # type: ignore[arg-type]
        inner_agent={"name": INNER, "kwargs": {}},
        work_dir=str(tmp_path / "gate-work"),
        **{"command": TOY_GATE, **options},
    )
    return agent, LocalEnvironment(workspace, shims_dir)


def _run(agent: GuardrailGateV2Agent, environment: LocalEnvironment) -> dict[str, Any]:
    context = AgentContext()
    asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))
    return cast(dict[str, Any], (context.metadata or {})["guardrail_gate"])


def _use(**with_: Any) -> Any:
    return compile_uses(use(REF, command="make check", **with_)).uses[0].context


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True).stdout


# ---- shadow mode ------------------------------------------------------------------------


def test_shadow_records_the_commit_check_but_never_refuses_or_touches_the_workspace(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims, mode="shadow")
    before = sorted(path.name for path in (tmp_path / "app").iterdir())

    gate = _run(agent, environment)

    assert [stage["stage"] for stage in gate["stages"]] == ["work"]
    assert ScriptedInnerAgent.prompts == ["Build the feature."], "the agent is never told"
    assert (gate["mode"], gate["shadow"], gate["termination_reason"]) == (
        "shadow",
        True,
        "shadow_recorded",
    )
    assert (gate[SIGNAL], gate["shadow_verdict"], gate["fix_rounds_used"]) == (None, False, 0)
    (run,) = gate["gate_runs"]
    assert (run["mode"], run["passed"], run["exit_code"], run["committed"]) == (
        "shadow",
        False,
        1,
        None,
    )
    assert run["shadow_committed"] is False
    assert run["checks_failed"] == ["style"]
    git = run["git"]
    assert (git["repository"], git["head_before"], git["head_after"], git["head_moved"]) == (
        "shadow",
        None,
        None,
        False,
    )
    assert sorted(git["status_porcelain"]) == ["A  feature.py", "A  main.py"]
    assert git["log_oneline"] == []
    assert gate["git_evidence"]["hook_exit_codes"] == [1]
    # The workspace is exactly what the agent left: no .git, no hook, no commit.
    after = sorted(path.name for path in (tmp_path / "app").iterdir())
    assert after == sorted([*before, "feature.py"])
    logs = tmp_path / "logs"
    assert json.loads((logs / "guardrail-gate" / GATE_RECORD).read_text()) == gate
    assert gate_signal(logs) is None, "a shadow gate decides no manipulation-check step"
    trajectory = Trajectory.model_validate_json((logs / "trajectory.json").read_text())
    assert trajectory.agent.version == "2"
    outcome = GUARDRAIL_GATE_V2.probe(_use(mode="shadow"), StepEvidence(logs), 0)
    assert outcome.passed, outcome.detail


def test_a_passing_shadow_check_commits_only_to_the_shadow_repository(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims, mode="shadow", command="echo clean")

    gate = _run(agent, environment)

    (run,) = gate["gate_runs"]
    assert (run["passed"], run["shadow_committed"], gate["shadow_verdict"]) == (True, True, True)
    git = run["git"]
    assert git["head_moved"] is True
    assert git["log_oneline"] == [
        f"{git['head_after'][:7]} guardrail-gate: commit after session (gate run 1)"
    ]
    assert git["status_porcelain"] == []
    assert not (tmp_path / "app" / ".git").exists()
    shadow_files = subprocess.run(
        ["git", "--git-dir", str(tmp_path / "gate-work" / "shadow" / ".git"), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert set(shadow_files) == {"main.py", "feature.py"}, ".venv stays out as in commit mode"


def test_shadow_leaves_an_agent_owned_repository_untouched(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(tmp_path, shims, mode="shadow", command="true")
    workspace = tmp_path / "app"
    subprocess.run(
        "git init -q . && git add main.py && "
        "git -c user.name=a -c user.email=a@b.invalid commit -qm mine",
        shell=True,
        cwd=workspace,
        check=True,
    )
    head = _git(workspace, "rev-parse", "HEAD")

    gate = _run(agent, environment)

    assert gate["gate_runs"][0]["passed"] is True
    assert _git(workspace, "rev-parse", "HEAD") == head
    assert _git(workspace, "diff", "--cached", "--name-only") == "", "its index is untouched"
    assert _git(workspace, "status", "--porcelain").splitlines() == ["?? .venv/", "?? feature.py"]


def test_a_shadow_gate_that_cannot_run_is_a_gate_error(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(tmp_path, shims, mode="shadow", command="./missing-gate.sh")

    gate = _run(agent, environment)

    assert (gate["termination_reason"], gate["shadow_verdict"]) == ("gate_error", None)
    assert [stage["stage"] for stage in gate["stages"]] == ["work"]
    outcome = GUARDRAIL_GATE_V2.probe(_use(mode="shadow"), StepEvidence(tmp_path / "logs"), 0)
    assert not outcome.passed


def test_a_shadow_session_that_raises_is_re_raised_after_the_record(
    tmp_path: Path, shims: Path
) -> None:
    ScriptedInnerAgent.fail = True
    agent, environment = _agent(tmp_path, shims, mode="shadow")
    context = AgentContext()

    with pytest.raises(RuntimeError, match="harness crashed"):
        asyncio.run(agent.run("Build it.", cast(BaseEnvironment, environment), context))

    gate = (context.metadata or {})["guardrail_gate"]
    assert (gate["termination_reason"], gate["gate_runs"], gate["git_evidence"]) == (
        "stage_failed",
        [],
        None,
    )


# ---- git evidence in commit mode ----------------------------------------------------------


def test_every_commit_attempt_records_git_evidence_of_the_refusal_and_the_commit(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims)

    gate = _run(agent, environment)

    assert (gate["mode"], gate["shadow"], gate["termination_reason"], gate[SIGNAL]) == (
        "commit",
        False,
        "gate_clean",
        True,
    )
    refused, passed = gate["gate_runs"]
    assert (refused["committed"], refused["git"]["head_moved"]) == (False, False)
    assert refused["git"]["head_after"] is None
    assert sorted(refused["git"]["status_porcelain"]) == ["A  feature.py", "A  main.py"]
    head = _git(tmp_path / "app", "rev-parse", "HEAD").strip()
    assert (passed["committed"], passed["git"]["head_after"], passed["git"]["head_moved"]) == (
        True,
        head,
        True,
    )
    assert passed["git"]["log_oneline"] == [
        f"{head[:7]} guardrail-gate: commit after session (gate run 2)"
    ]
    assert passed["git"]["status_porcelain"] == []
    evidence = gate["git_evidence"]
    assert (evidence["repository"], evidence["head_after"]) == ("workspace", head)
    assert (evidence["hook_exit_codes"], evidence["git_commit_exit_codes"]) == ([1, 0], [1, 0])
    assert evidence["commits_recorded"] == 1
    assert len(ScriptedInnerAgent.prompts) == 2


def test_command_mode_records_no_git_evidence(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(tmp_path, shims, mode="command", command="echo all good")

    gate = _run(agent, environment)

    assert gate["gate_runs"][0]["git"] is None
    assert gate["git_evidence"] is None


def test_git_lines_never_reach_the_key_value_parse() -> None:
    stdout = (
        "repo=workspace\nhead_before=\ngit_rc=1\nrc=1\nhead_after=\nbytes=3\nsha256=x\n"
        f"{GIT_LOG_MARK}\nabc1234 rc=0 message with = sign\n"
        f"{GIT_STATUS_MARK}\nA  a=b.py\n"
        "----raidar-guardrail-gate-output----\nno\n"
    )

    run = read_gate_run(stdout, run=1, after="w", mode=GateModeV2.COMMIT, log="x")

    assert (run.gate.exit_code, run.gate.git_exit_code, run.gate.output) == (1, 1, "no\n")
    assert run.git is not None
    assert run.git.log == ("abc1234 rc=0 message with = sign",)
    assert run.git.status == ("A  a=b.py",)


# ---- configuration and identity -----------------------------------------------------------


def test_v2_is_a_new_version_and_v1_stays_frozen() -> None:
    registry = ComponentRegistry()
    v1, v2 = registry.get("guardrail-gate@1"), registry.get(REF)

    assert fingerprint(v1) != fingerprint(v2)
    assert "raidar/components/guardrail_gate_v2.py" not in GUARDRAIL_GATE.manifest.fingerprint
    assert set(GUARDRAIL_GATE.manifest.fingerprint) < set(v2.manifest.fingerprint)
    wrapper = compile_uses(use(REF, command="make check", mode="shadow")).agent_wrapper
    assert wrapper is not None
    assert wrapper.import_path == GUARDRAIL_GATE_V2_IMPORT_PATH
    assert dict(wrapper.kwargs)["mode"] == "shadow"
    assert dict(wrapper.kwargs)["component"] == REF


@pytest.mark.parametrize(
    ("ref", "mode", "message"),
    [
        ("guardrail-gate@1", "shadow", "'with.mode' must be 'commit' or 'command'"),
        (REF, "dry-run", "'with.mode' must be one of commit, command, shadow"),
    ],
)
def test_shadow_is_a_v2_mode_only(ref: str, mode: str, message: str) -> None:
    with pytest.raises(ComponentRejectedError, match=message):
        compile_uses(use(ref, command="make check", mode=mode))
