"""guardrail-gate: a deterministic commit gate between the agent's sessions.

A scripted inner Harbor agent stands in for the harness and a local-shell
environment for the container (mounted logs), so the gate's real shell script
(git init, the pre-commit hook, ``git commit``, output capture) runs for real.
Shadow mode must record the same commit check without writing the workspace or
starting a fix session; every commit attempt records ``HEAD`` before and after,
``git log --oneline`` and ``git status --porcelain``. A toy autofix command
stands in for the pack's (it rewrites ``x  = 1`` as ``x = 1`` and writes the
report the contract asks for); a toy gate refuses ``x  =``: enforced, the
autofix runs before every commit check and a failing autofix is typed and the
check runs without it; in shadow mode the workspace stays byte-identical, the
agent sees nothing, and the fixed copy's check is recorded beside the raw one.
On OrbStack, a real two-step Harbor trial on the pack image with the nop agent
and a toy gate that fails once then passes per step proves the gate runs in the
container and that a planted failure refuses the commit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory
from harbor.models.trial.config import AgentConfig

from raidar.subject.agent.config import Harness
from raidar.subject.treatment import ComponentRegistry, ComponentRejectedError, StepEvidence
from raidar.subject.treatment.catalog.gate import (
    GUARDRAIL_GATE,
    GUARDRAIL_GATE_IMPORT_PATH,
    gate_signal,
)
from raidar.subject.treatment.definition.registry import fingerprint
from raidar.subject.treatment.runtime.autofix_run import (
    ERROR_TYPES,
    REPORT_MARK,
    AutofixMode,
    AutofixRun,
    read_autofix_run,
)
from raidar.subject.treatment.runtime.gate_run import (
    GATE_RECORD,
    GIT_LOG_MARK,
    GIT_STATUS_MARK,
    MAX_OUTPUT_CHARS,
    SIGNAL,
    GateMode,
    fix_prompt,
    parse_checks,
    read_gate_run,
)
from raidar.subject.treatment.runtime.guardrail_gate import GuardrailGateAgent
from raidar.subject.treatment.runtime.review_loop import PROMPT_ARG_BUDGET
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    build_task,
    docker_target_ready,
    run_trial_sync,
)

REF = "guardrail-gate@4"
INNER = "tests.test_guardrail_gate_workflow:ScriptedInnerAgent"
LAYOUT_INNER = "tests.test_guardrail_gate_workflow:LayoutInnerAgent"
# Fails until the agent has written fixed.txt; prints a recognised check line.
TOY_GATE = (
    'if [ -f fixed.txt ]; then echo "gate-check style pass"; '
    'else echo "style: feature.py:1 missing docstring"; echo "gate-check style fail"; exit 1; fi'
)
LAYOUT_GATE = (
    "if grep -q 'x  =' feature.py; then echo 'feature.py:1:1: FORMAT not formatted'; "
    'echo "gate-check format fail"; exit 1; else echo "gate-check format pass"; fi'
)
REPORT = {
    "schema": "toy-autofix/1",
    "verdict": "fixed",
    "files_changed": ["feature.py"],
    "lines_changed": 2,
    "codes_fixed": {"FORMAT": 1},
    "codes_fixed_blocking": {"FORMAT": 1},
    "error": None,
    "error_type": None,
}
TOY_AUTOFIX = (
    "sed -i.bak 's/x  =/x =/' feature.py && rm -f feature.py.bak && "
    f"printf '%s' {shlex.quote(json.dumps(REPORT))} > \"$RAIDAR_GATE_REPORT_DIR/autofix-1.json\""
)


@dataclass
class _Capabilities:
    mounted: bool = True


@dataclass
class LocalEnvironment:
    """Runs commands in a local workspace; the logs dir is 'mounted' on the host."""

    workspace: Path
    path_prefix: Path
    capabilities: _Capabilities = field(default_factory=_Capabilities)

    async def exec(self, command: str, **_: Any) -> ExecResult:
        env = {**os.environ, "PATH": f"{self.path_prefix}:{os.environ['PATH']}"}
        done = subprocess.run(
            ["bash", "-c", command], cwd=self.workspace, capture_output=True, text=True, env=env
        )
        return ExecResult(stdout=done.stdout, stderr=done.stderr, return_code=done.returncode)


def _trajectory(session: int) -> str:
    return json.dumps(
        {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{session}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "done"}],
        }
    )


class ScriptedInnerAgent(BaseAgent):
    """Writes feature.py when working; writes fixed.txt in fix sessions (if told to)."""

    prompts: ClassVar[list[str]] = []
    fixes: ClassVar[bool] = True
    fail: ClassVar[bool] = False

    @staticmethod
    def name() -> str:
        return "scripted-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        cls = type(self)
        cls.prompts.append(instruction)
        if cls.fail:
            raise RuntimeError("harness crashed")
        fixing = "The gate's output:" in instruction
        write = "fixed.txt" if fixing and cls.fixes else "feature.py"
        await environment.exec(
            f"echo 'x = {len(cls.prompts)}' >> {write} && "
            f"echo {shlex.quote(_trajectory(len(cls.prompts)))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 100, 10, 0.01


class LayoutInnerAgent(BaseAgent):
    """Work: appends an unformatted ``x  = n`` to feature.py; fix: rewrites it formatted."""

    prompts: ClassVar[list[str]] = []

    @staticmethod
    def name() -> str:
        return "layout-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        cls = type(self)
        cls.prompts.append(instruction)
        fixing = "The gate's output:" in instruction
        command = "echo 'x = 0' > feature.py" if fixing else "echo 'x  = 1' >> feature.py"
        await environment.exec(
            f"{command} && echo {shlex.quote(_trajectory(len(cls.prompts)))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 100, 10, 0.01


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    ScriptedInnerAgent.prompts.clear()
    ScriptedInnerAgent.fixes = True
    ScriptedInnerAgent.fail = False
    LayoutInnerAgent.prompts.clear()


@pytest.fixture
def shims(tmp_path: Path) -> Path:
    """A pass-through ``timeout`` where the host has none (macOS); Linux keeps its own."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        shim = bin_dir / "timeout"
        shim.write_text('#!/bin/sh\n[ "$1" = "-k" ] && shift 2\nshift\nexec "$@"\n')
        shim.chmod(0o755)
    return bin_dir


def _agent(
    tmp_path: Path, shims: Path, **options: Any
) -> tuple[GuardrailGateAgent, LocalEnvironment]:
    workspace, logs = tmp_path / "app", tmp_path / "logs"
    workspace.mkdir(parents=True)
    logs.mkdir()
    (workspace / "main.py").write_text("print('hi')\n")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "big.bin").write_text("not for the repository\n")
    (logs / "raidar-reset.json").write_text("{}")
    agent = GuardrailGateAgent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=logs,  # type: ignore[arg-type]
        inner_agent={
            "name": options.pop("inner", INNER),
            "kwargs": options.pop("inner_kwargs", {}),
        },
        work_dir=str(tmp_path / "gate-work"),
        **{"command": TOY_GATE, **options},
    )
    return agent, LocalEnvironment(workspace, shims)


def _autofix_agent(
    tmp_path: Path, shims: Path, **options: Any
) -> tuple[GuardrailGateAgent, LocalEnvironment]:
    """The layout inner agent and the layout gate; two fix rounds at most."""
    defaults = {"inner": LAYOUT_INNER, "command": LAYOUT_GATE, "max_rounds": 2, "timeout_sec": 60}
    return _agent(tmp_path, shims, **{**defaults, **options})


def _run_context(agent: GuardrailGateAgent, environment: LocalEnvironment) -> AgentContext:
    context = AgentContext()
    asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))
    return context


def _run(agent: GuardrailGateAgent, environment: LocalEnvironment) -> dict[str, Any]:
    return cast(dict[str, Any], (_run_context(agent, environment).metadata or {})["guardrail_gate"])


def _git(workspace: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True)
    return done.stdout


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _use(**with_: Any) -> Any:
    return compile_uses(use(REF, command="make check", **with_)).uses[0].context


# ---- the loop: refusal, fix session, gate again ---------------------------------------------


def test_refused_commit_starts_a_fix_session_with_the_exact_gate_output(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims)

    context = _run_context(agent, environment)

    gate = context.metadata["guardrail_gate"]  # type: ignore[index]
    assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix"]
    assert (gate["termination_reason"], gate[SIGNAL], gate["fix_rounds_used"]) == (
        "gate_clean",
        True,
        1,
    )
    first, second = gate["gate_runs"]
    assert (first["passed"], first["committed"], first["exit_code"]) == (False, False, 1)
    assert (first["checks_failed"], first["checks_passed"]) == (["style"], [])
    assert (second["passed"], second["committed"], second["checks_passed"]) == (
        True,
        True,
        ["style"],
    )
    assert first["output_sha256"] != second["output_sha256"]
    logs = tmp_path / "logs"
    refused = (logs / first["log"]).read_text()
    assert "style: feature.py:1 missing docstring\ngate-check style fail\n" in refused
    work_prompt, fix = ScriptedInnerAgent.prompts
    assert work_prompt == "Build the feature."
    assert "refused your commit" in fix
    assert "exit status 1" in fix
    assert refused in fix
    assert "Build the feature." in fix
    # The refused commit never landed; the passing one holds the fix, not .venv.
    workspace = tmp_path / "app"
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "1"
    assert set(_git(workspace, "ls-files").split()) == {"main.py", "feature.py", "fixed.txt"}
    assert gate["repo_created"] is True
    # Both sessions are billed to the arm and embedded in the step's trajectory.
    assert (context.n_input_tokens, context.n_output_tokens) == (200, 20)
    assert context.cost_usd == pytest.approx(0.02)
    trajectory = Trajectory.model_validate_json((logs / "trajectory.json").read_text())
    assert trajectory.agent.version == "4"
    assert [sub.trajectory_id for sub in trajectory.subagent_trajectories or []] == [
        "stage_1_work",
        "stage_2_fix",
    ]
    assert (logs / "raidar-reset.json").is_file(), "step-level logs stay at the top level"
    assert json.loads((logs / "guardrail-gate" / GATE_RECORD).read_text()) == gate
    assert gate_signal(logs) is True
    outcome = GUARDRAIL_GATE.probe(_use(), StepEvidence(logs), 0)
    assert outcome.passed, outcome.detail


def test_a_gate_that_keeps_failing_ends_the_step_unclean_after_max_rounds(
    tmp_path: Path, shims: Path
) -> None:
    ScriptedInnerAgent.fixes = False
    agent, environment = _agent(tmp_path, shims, max_rounds=2)

    context = _run_context(agent, environment)

    gate = context.metadata["guardrail_gate"]  # type: ignore[index]
    assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix", "fix"]
    assert [run["passed"] for run in gate["gate_runs"]] == [False, False, False]
    assert (gate["termination_reason"], gate[SIGNAL]) == ("rounds_exhausted", False)
    assert gate["final_checks_failed"] == ["style"]
    assert _git(tmp_path / "app", "rev-list", "--count", "HEAD").strip() == ""
    assert context.cost_usd == pytest.approx(0.03)
    assert gate_signal(tmp_path / "logs") is False
    assert GUARDRAIL_GATE.probe(_use(), StepEvidence(tmp_path / "logs"), 0).passed


def test_command_mode_runs_the_gate_alone_and_a_clean_gate_needs_no_fix(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims, mode="command", command="echo all good")

    gate = _run(agent, environment)

    assert [stage["stage"] for stage in gate["stages"]] == ["work"]
    (run,) = gate["gate_runs"]
    assert (run["passed"], run["committed"], run["checks_parsed"]) == (True, None, False)
    assert run["checks_failed"] is None
    assert (gate["termination_reason"], gate[SIGNAL]) == ("gate_clean", True)
    assert not (tmp_path / "app" / ".git").exists()


@pytest.mark.parametrize("mode", ["commit", "command"])
def test_a_gate_that_cannot_run_is_infrastructure_not_a_fix_round(
    tmp_path: Path, shims: Path, mode: str
) -> None:
    agent, environment = _agent(tmp_path, shims, mode=mode, command="./missing-gate.sh")

    gate = _run(agent, environment)

    assert [stage["stage"] for stage in gate["stages"]] == ["work"]
    assert gate["termination_reason"] == "gate_error"
    assert "not runnable (exit 127)" in gate["error"]
    assert gate[SIGNAL] is None
    outcome = GUARDRAIL_GATE.probe(_use(), StepEvidence(tmp_path / "logs"), 0)
    assert (outcome.passed, "could not run at the first step" in outcome.detail) == (False, True)


def test_a_session_that_raises_is_re_raised_after_the_record(tmp_path: Path, shims: Path) -> None:
    ScriptedInnerAgent.fail = True
    agent, environment = _agent(tmp_path, shims)
    context = AgentContext()

    with pytest.raises(RuntimeError, match="harness crashed"):
        asyncio.run(agent.run("Build it.", cast(BaseEnvironment, environment), context))

    gate = context.metadata["guardrail_gate"]  # type: ignore[index]
    assert (gate["termination_reason"], gate["gate_runs"], gate[SIGNAL]) == (
        "stage_failed",
        [],
        None,
    )
    outcome = GUARDRAIL_GATE.probe(_use(), StepEvidence(tmp_path / "logs"), 0)
    assert outcome.passed
    assert "classified by its exception" in outcome.detail


def test_the_prompt_template_wraps_the_task_but_not_the_fix_instructions(
    tmp_path: Path, shims: Path
) -> None:
    template = tmp_path / "wrapper.md"
    template.write_text("STARTER FRAMING\n\n{{ instruction }}\n")
    agent, environment = _agent(
        tmp_path, shims, inner_kwargs={"prompt_template_path": str(template)}
    )

    _run(agent, environment)

    work_prompt, fix = ScriptedInnerAgent.prompts
    assert work_prompt.startswith("STARTER FRAMING")
    assert "Build the feature." in work_prompt
    assert fix.startswith("You were working on the task below")
    assert "STARTER FRAMING" in fix


def test_a_pre_fix_snapshot_is_recorded_for_every_fix_session(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(tmp_path, shims)

    gate = _run(agent, environment)

    # The local environment cannot download a directory: the gap is recorded, the step goes on.
    (snapshot,) = gate["pre_fix_snapshots"]
    assert (snapshot["round"], snapshot["before_session"]) == (1, "stage_2_fix")
    assert snapshot["path"] == "guardrail-gate/pre_fix_1/app"
    assert snapshot["tree_digest"] is None
    assert snapshot["error"]
    assert gate["termination_reason"] == "gate_clean"


# ---- parsing and prompts ---------------------------------------------------------------------


def test_check_lines_are_parsed_from_every_recognised_format() -> None:
    output = "\n".join(
        [
            "ruff.....................................................................Failed",
            "- hook id: ruff",
            "mypy.....................................................................Passed",
            "pytest...............................(no files to check)Skipped",
            "gate-check lint-imports fail",
            "gate-check complexity pass",
            "make: *** [Makefile:12: typecheck] Error 1",
        ]
    )

    checks = parse_checks(output)

    assert checks.formats == ("pre-commit", "gate-check", "make")
    assert checks.failed == ("ruff", "lint-imports", "typecheck")
    assert checks.passed == ("mypy", "complexity")
    assert not parse_checks("All checks passed!\n").parsed


def test_a_gate_timeout_is_a_failure_the_agent_is_told_about() -> None:
    stdout = "rc=124\nbytes=5\nsha256=abc\n----raidar-guardrail-gate-output----\nslow\n"
    run = read_gate_run(stdout, run=1, after="stage_1_work", mode=GateMode.COMMAND, log="x.log")

    assert (run.timed_out, run.passed, run.error) == (True, False, None)
    assert "did not finish within 600 seconds" in fix_prompt("Task.", run, "make check", 600)
    killed = read_gate_run(
        "git_rc=124\nrc=\nbytes=0\nsha256=e3\n----raidar-guardrail-gate-output----\n",
        run=2,
        after="stage_2_fix",
        mode=GateMode.COMMIT,
        log="y.log",
    )
    assert (killed.timed_out, killed.error, killed.exit_code) == (True, None, None)


def test_fix_prompt_fits_the_harness_argument_limit() -> None:
    output = "E" * MAX_OUTPUT_CHARS + "'" * MAX_OUTPUT_CHARS
    stdout = f"rc=1\nbytes=1\nsha256=a\n----raidar-guardrail-gate-output----\n{output}"
    run = read_gate_run(stdout, run=1, after="stage_1_work", mode=GateMode.COMMAND, log="x")

    prompt = fix_prompt("Task.", run, "make check", 600)

    assert len(prompt.encode()) + 4 * prompt.count("'") <= PROMPT_ARG_BUDGET
    assert "truncated to fit the harness prompt limit" in prompt


def test_git_lines_never_reach_the_key_value_parse() -> None:
    stdout = (
        "repo=workspace\nhead_before=\ngit_rc=1\nrc=1\nhead_after=\nbytes=3\nsha256=x\n"
        f"{GIT_LOG_MARK}\nabc1234 rc=0 message with = sign\n"
        f"{GIT_STATUS_MARK}\nA  a=b.py\n"
        "----raidar-guardrail-gate-output----\nno\n"
    )

    run = read_gate_run(stdout, run=1, after="w", mode=GateMode.COMMIT, log="x")

    assert (run.exit_code, run.git_exit_code, run.output) == (1, 1, "no\n")
    assert run.git is not None
    assert run.git.log == ("abc1234 rc=0 message with = sign",)
    assert run.git.status == ("A  a=b.py",)


# ---- shadow mode ------------------------------------------------------------------------------


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
    outcome = GUARDRAIL_GATE.probe(_use(mode="shadow"), StepEvidence(logs), 0)
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
    outcome = GUARDRAIL_GATE.probe(_use(mode="shadow"), StepEvidence(tmp_path / "logs"), 0)
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


# ---- git evidence in commit mode ---------------------------------------------------------------


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


# ---- enforced autofix: autofix, re-stage, commit -----------------------------------------------


def test_enforced_autofix_runs_before_the_commit_so_a_layout_refusal_never_happens(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _autofix_agent(
        tmp_path, shims, autofix_before_check=True, autofix_command=TOY_AUTOFIX
    )

    gate = _run(agent, environment)

    assert (gate["workflow"], gate["termination_reason"], gate[SIGNAL]) == (
        REF,
        "gate_clean",
        True,
    )
    assert [stage["stage"] for stage in gate["stages"]] == ["work"], "no fix session"
    (run,) = gate["gate_runs"]
    assert (run["passed"], run["committed"]) == (True, True)
    autofix = gate["autofix"]
    assert (autofix["enabled"], autofix["mode"], autofix["errors"]) == (True, "apply", 0)
    (fix,) = autofix["runs"]
    assert (fix["before_gate_run"], fix["after"], fix["fallback"], fix["error_type"]) == (
        1,
        "stage_1_work",
        False,
        None,
    )
    assert (fix["files_changed"], fix["lines_changed"], fix["codes_fixed"]) == (
        ["feature.py"],
        2,
        {"FORMAT": 1},
    )
    assert fix["report_schema"] == "toy-autofix/1"
    workspace = tmp_path / "app"
    assert (workspace / "feature.py").read_text() == "x = 1\n"
    committed = subprocess.run(
        ["git", "show", "HEAD:feature.py"], cwd=workspace, capture_output=True, text=True
    ).stdout
    assert committed == "x = 1\n", "the commit holds the fixed, re-staged tree"
    logs = tmp_path / "logs" / "guardrail-gate"
    assert (logs / "autofix_run_1.log").is_file()
    assert json.loads((logs / "autofix_run_1" / "autofix-1.json").read_text()) == REPORT
    outcome = GUARDRAIL_GATE.probe(
        _use(autofix_before_check=True, autofix_command="guardrail-check --autofix"),
        StepEvidence(tmp_path / "logs"),
        0,
    )
    assert outcome.passed, outcome.detail
    assert "autofix 1 pass(es), 0 error(s)" in outcome.detail


def test_the_autofix_runs_again_before_every_re_check_after_a_fix_session(
    tmp_path: Path, shims: Path
) -> None:
    # The toy gate also refuses until fixed.txt exists, so a fix session must run.
    gate_cmd = f"[ -f fixed.txt ] || {{ echo 'gate-check tests fail'; exit 1; }}; {LAYOUT_GATE}"
    agent, environment = _autofix_agent(
        tmp_path,
        shims,
        command=gate_cmd,
        autofix_before_check=True,
        autofix_command=f"touch fixed.txt.pending; {TOY_AUTOFIX}",
    )

    gate = _run(agent, environment)

    runs = gate["autofix"]["runs"]
    assert [run["before_gate_run"] for run in runs] == [1, 2, 3]
    assert [run["after"] for run in runs] == ["stage_1_work", "stage_2_fix", "stage_3_fix"]
    assert len(gate["gate_runs"]) == 3
    assert gate["termination_reason"] == "rounds_exhausted"


@pytest.mark.parametrize(
    ("command", "error_type"),
    [
        ("exit 3", "command_failed"),
        ("true", "report_missing"),
        ("no-such-autofix-command", "not_runnable"),
        ('mkdir -p x && echo nope > "$RAIDAR_GATE_REPORT_DIR/a.json"', "report_invalid"),
        (
            'echo \'{"verdict": "error", "error": "ruff missing", "error_type": "tool_missing"}\''
            ' > "$RAIDAR_GATE_REPORT_DIR/a.json"; exit 126',
            "command_error",
        ),
    ],
)
def test_a_failed_autofix_is_typed_and_the_gate_runs_without_it(
    tmp_path: Path, shims: Path, command: str, error_type: str
) -> None:
    agent, environment = _autofix_agent(
        tmp_path, shims, autofix_before_check=True, autofix_command=command
    )

    gate = _run(agent, environment)

    first = gate["autofix"]["runs"][0]
    assert (first["error_type"], first["fallback"], first["files_changed"]) == (
        error_type,
        True,
        None,
    )
    assert first["error"]
    assert gate["autofix"]["errors"] == len(gate["autofix"]["runs"])
    # Without autofix: the unformatted tree is refused and the fix session repairs it.
    assert gate["gate_runs"][0]["passed"] is False
    assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix"]
    assert (gate["termination_reason"], gate[SIGNAL]) == ("gate_clean", True)
    assert error_type in ERROR_TYPES


def test_without_autofix_the_record_says_it_is_off(tmp_path: Path, shims: Path) -> None:
    agent, environment = _autofix_agent(tmp_path, shims)

    gate = _run(agent, environment)

    assert gate["autofix"] == {
        "enabled": False,
        "command": None,
        "mode": None,
        "runs": [],
        "errors": 0,
    }
    assert gate["gate_runs"][0]["passed"] is False
    assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix"]
    assert "pre_fix_snapshots" in gate


# ---- shadow autofix: a throwaway copy, the workspace untouched ---------------------------------


def test_shadow_autofix_checks_a_fixed_copy_and_leaves_the_workspace_byte_identical(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _autofix_agent(
        tmp_path, shims, mode="shadow", autofix_before_check=True, autofix_command=TOY_AUTOFIX
    )
    workspace = tmp_path / "app"
    expected = {**_tree(workspace), "feature.py": hashlib.sha256(b"x  = 1\n").hexdigest()}

    gate = _run(agent, environment)

    assert _tree(workspace) == expected, "the workspace is byte-identical to the agent's tree"
    assert sorted(path.name for path in workspace.iterdir()) == [".venv", "feature.py", "main.py"]
    assert LayoutInnerAgent.prompts == ["Build the feature."], "the agent is never told"
    assert (gate["termination_reason"], gate["shadow_verdict"], gate[SIGNAL]) == (
        "shadow_recorded",
        False,
        None,
    )
    (raw,) = gate["gate_runs"]
    assert raw["passed"] is False
    (fix,) = gate["autofix"]["runs"]
    assert (fix["mode"], fix["after_gate_run"], fix["before_gate_run"], fix["error_type"]) == (
        "dry_run",
        1,
        None,
        None,
    )
    assert fix["codes_fixed"] == {"FORMAT": 1}
    check = fix["post_check"]
    assert (check["passed"], check["mode"], check["error"]) == (True, "shadow", None)
    assert check["log"] == "guardrail-gate/post_autofix_run_1.log"
    assert check["git"]["repository"] == "shadow"
    assert not (tmp_path / "gate-work" / "autofix").exists(), "the copy is deleted"
    logs = tmp_path / "logs" / "guardrail-gate"
    assert (logs / "post_autofix_run_1.log").read_text().strip() == "gate-check format pass"
    outcome = GUARDRAIL_GATE.probe(
        _use(mode="shadow", autofix_before_check=True, autofix_command="x"),
        StepEvidence(tmp_path / "logs"),
        0,
    )
    assert outcome.passed, outcome.detail


def test_the_shadow_copy_leaves_out_the_excludes_and_links_the_venv(
    tmp_path: Path, shims: Path
) -> None:
    probe = (
        'printf "%s\\n" "$(pwd -P)" "$(readlink .venv)" "$([ -e .git ] && echo git)" > '
        f"{tmp_path}/copy.txt; {TOY_AUTOFIX}"
    )
    agent, environment = _autofix_agent(
        tmp_path, shims, mode="shadow", autofix_before_check=True, autofix_command=probe
    )
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path / "app", check=True)

    _run(agent, environment)

    where, venv, git = (tmp_path / "copy.txt").read_text().splitlines()
    assert where.endswith("/gate-work/autofix/app")
    assert venv == str((tmp_path / "app" / ".venv").resolve())
    assert git == "", "the copy has no .git"


def test_a_failed_shadow_autofix_is_typed_with_no_post_check(tmp_path: Path, shims: Path) -> None:
    agent, environment = _autofix_agent(
        tmp_path, shims, mode="shadow", autofix_before_check=True, autofix_command="exit 4"
    )

    gate = _run(agent, environment)

    (fix,) = gate["autofix"]["runs"]
    assert (fix["error_type"], fix["fallback"], fix["post_check"]) == (
        "command_failed",
        False,
        None,
    )
    assert gate["termination_reason"] == "shadow_recorded"
    assert (tmp_path / "app" / "feature.py").read_text() == "x  = 1\n"


def test_a_shadow_gate_error_runs_no_autofix(tmp_path: Path, shims: Path) -> None:
    agent, environment = _autofix_agent(
        tmp_path,
        shims,
        mode="shadow",
        command="./missing-gate.sh",
        autofix_before_check=True,
        autofix_command=TOY_AUTOFIX,
    )

    gate = _run(agent, environment)

    assert (gate["termination_reason"], gate["autofix"]["runs"]) == ("gate_error", [])


# ---- reading an autofix pass back ---------------------------------------------------------------


def _base(mode: AutofixMode = AutofixMode.APPLY) -> AutofixRun:
    return AutofixRun(1, "stage_1_work", mode, "l.log", "l", before_gate_run=1)


@pytest.mark.parametrize(
    ("stdout", "error_type"),
    [
        ("copy_rc=1\n", "copy_failed"),
        ("", "exec_failed"),
        ("rc=124\nreports=0\n", "timeout"),
        ("rc=137\nreports=1\n", "timeout"),
        (f'rc=0\nreports=1\n{REPORT_MARK}\n{{"verdict": "unchanged"}}', None),
    ],
)
def test_read_autofix_run_types_every_outcome(stdout: str, error_type: str | None) -> None:
    run = read_autofix_run(stdout, _base(AutofixMode.DRY_RUN))
    assert run.error_type == error_type
    assert run.ok is (error_type is None)


# ---- the built-in ------------------------------------------------------------------------------


def test_the_same_wrapper_runs_on_codex_and_claude_code() -> None:
    gate_use = use(REF, command="make check", max_rounds=2)
    codex = compile_uses(gate_use, harness=Harness.CODEX_CLI)
    claude = compile_uses(gate_use, harness=Harness.CLAUDE_CODE)

    assert codex.agent_wrapper == claude.agent_wrapper
    assert codex.agent_wrapper is not None
    assert dict(codex.agent_wrapper.kwargs) == {
        "command": "make check",
        "mode": "commit",
        "max_rounds": 2,
        "timeout_sec": 600,
        "component": REF,
    }
    assert not codex.native_config, "no harness hooks"
    assert not claude.native_config, "no harness hooks"
    applied = claude.apply_to_agent_config(AgentConfig(name="claude-code"))
    assert applied.import_path == GUARDRAIL_GATE_IMPORT_PATH
    assert applied.kwargs["inner_agent"] == {"name": "claude-code", "kwargs": {}}


def test_the_builtin_passes_the_mode_and_autofix_options_to_the_wrapper() -> None:
    compiled = compile_uses(
        use(
            REF,
            command="guardrail-check",
            mode="shadow",
            autofix_before_check=True,
            autofix_command="guardrail-check --autofix",
        )
    )
    wrapper = compiled.agent_wrapper
    assert wrapper is not None
    assert wrapper.import_path == GUARDRAIL_GATE_IMPORT_PATH
    kwargs = dict(wrapper.kwargs)
    assert kwargs["mode"] == "shadow"
    assert (kwargs["autofix_before_check"], kwargs["autofix_command"], kwargs["component"]) == (
        True,
        "guardrail-check --autofix",
        REF,
    )
    off = compile_uses(use(REF, command="guardrail-check")).agent_wrapper
    assert off is not None
    assert "autofix_before_check" not in dict(off.kwargs)


@pytest.mark.parametrize(
    ("with_", "message"),
    [
        ({}, "missing required 'with' keys \\['command'\\]"),
        ({"command": " "}, "non-empty string"),
        ({"command": "pytest /tests/test_outputs.py"}, "hidden verifier path"),
        ({"command": "make check", "mode": "hook"}, "one of commit, command, shadow"),
        ({"command": "make check", "mode": "dry-run"}, "one of commit, command, shadow"),
        ({"command": "make check", "max_rounds": 0}, "integer 1..5"),
        ({"command": "make check", "timeout_sec": True}, "integer 10..3600"),
        ({"command": "make check", "reviewer": "x"}, "unknown 'with' keys"),
        ({"command": "make check", "autofix_command": "fix --safe"}, "autofix_command"),
        ({"command": "make check", "autofix_before_check": True}, "autofix_command"),
        (
            {"command": "make check", "autofix_before_check": "yes", "autofix_command": "x"},
            "true or false",
        ),
        (
            {"command": "make check", "autofix_before_check": True, "autofix_command": "  "},
            "safe-fix command",
        ),
        (
            {
                "command": "make check",
                "autofix_before_check": True,
                "autofix_command": "cat /tests/test_x.py",
            },
            "hidden verifier path",
        ),
        (
            {
                "command": "make check",
                "mode": "command",
                "autofix_before_check": True,
                "autofix_command": "x",
            },
            "not in 'command' mode",
        ),
        ({"command": "make check", "autofix_timeout": 3}, "unknown 'with' keys"),
    ],
)
def test_invalid_configuration_rejects_the_arm(with_: dict[str, Any], message: str) -> None:
    with pytest.raises(ComponentRejectedError, match=message):
        compile_uses(use(REF, **with_))


def test_the_gate_and_another_workflow_cannot_both_wrap_the_agent() -> None:
    with pytest.raises(ComponentRejectedError, match="more than one workflow wraps the agent"):
        compile_uses(
            use(REF, command="make check"),
            use("review-loop@2", max_review_rounds=2),
        )


def test_the_probe_fails_when_autofix_was_asked_for_but_not_recorded(tmp_path: Path) -> None:
    gate_dir = tmp_path / "guardrail-gate"
    gate_dir.mkdir()
    record = {
        "termination_reason": "gate_clean",
        "gate_runs": [{"run": 1, "error": None, "log": "x"}],
        "fix_rounds_used": 0,
        SIGNAL: True,
    }
    (gate_dir / GATE_RECORD).write_text(json.dumps(record))
    use_ctx = _use(autofix_before_check=True, autofix_command="guardrail-check --autofix")

    outcome = GUARDRAIL_GATE.probe(use_ctx, StepEvidence(tmp_path), 0)

    assert not outcome.passed
    assert GUARDRAIL_GATE.probe(_use(), StepEvidence(tmp_path), 0).passed


def test_the_fingerprint_covers_the_gate_and_autofix_modules() -> None:
    files = GUARDRAIL_GATE.manifest.fingerprint
    assert "orchestrator/src/raidar/subject/treatment/runtime/autofix_run.py" in files
    assert "orchestrator/src/raidar/subject/treatment/runtime/guardrail_gate.py" in files
    assert "orchestrator/src/raidar/subject/treatment/runtime/gate_run.py" in files
    assert len(files) == len(set(files))
    assert fingerprint(ComponentRegistry().get(REF)) != ""


docker_trial = pytest.mark.skipif(
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)


@docker_trial
def test_planted_failure_refuses_the_commit_in_a_real_two_step_harbor_trial(
    tmp_path: Path,
) -> None:
    compiled = compile_uses(
        use("workspace-files@1", ("components/gate/toy-gate.sh",)),
        use(REF, command="bash toy-gate.sh", max_rounds=2, timeout_sec=60),
    )
    task = tmp_path / "guardrail-gate"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})
    assert compiled.agent_wrapper is not None
    agent = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": "nop"}},
    )

    trial = run_trial_sync(task, tmp_path / "trials", agent, task.name)

    for index, step in enumerate(("checkpoint_1", "checkpoint_2")):
        agent_dir = trial / "steps" / step / "agent"
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        gate = json.loads((agent_dir / "guardrail-gate" / GATE_RECORD).read_text())
        assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix"]
        refused, passed = gate["gate_runs"]
        assert (refused["passed"], refused["committed"], refused["exit_code"]) == (False, False, 1)
        assert refused["checks_failed"] == ["planted-failure"]
        assert (passed["passed"], passed["committed"]) == (True, True)
        assert (gate["termination_reason"], gate[SIGNAL]) == ("gate_clean", True)
        assert gate["repo_created"] is (index == 0)
        # The planted failure refused step 1's first commit: no commit existed when
        # its second run's hook ran, and exactly one when step 2's gate first ran.
        commits = [
            (agent_dir / run["log"]).read_text().split("commits so far: ")[1].split()[0]
            for run in gate["gate_runs"]
        ]
        assert commits == (["0", "0"] if index == 0 else ["1", "1"])
        assert gate_signal(agent_dir) is True
