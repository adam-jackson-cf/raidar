"""guardrail-gate@1: a deterministic commit gate between the agent's sessions.

A scripted inner Harbor agent stands in for the harness and a local-shell
environment for the container (mounted logs), so the gate's real shell script
(git init, the pre-commit hook, ``git commit``, output capture) runs for real.
On OrbStack, a real two-step Harbor trial on the pack image with the nop agent
and a toy gate that fails once then passes per step proves the gate runs in the
container and that a planted failure refuses the commit.
"""

from __future__ import annotations

import asyncio
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

from raidar.agents.config import Harness
from raidar.components import ComponentRejectedError, StepEvidence
from raidar.components.catalog.gate import (
    GUARDRAIL_GATE,
    GUARDRAIL_GATE_IMPORT_PATH,
    gate_signal,
)
from raidar.components.runtime.gate_run import (
    GATE_RECORD,
    MAX_OUTPUT_CHARS,
    SIGNAL,
    GateMode,
    fix_prompt,
    parse_checks,
    read_gate_run,
)
from raidar.components.runtime.guardrail_gate import GuardrailGateAgent
from raidar.components.runtime.review_loop import PROMPT_ARG_BUDGET
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    build_task,
    docker_target_ready,
    run_trial_sync,
)

INNER = "tests.test_guardrail_gate_workflow:ScriptedInnerAgent"
# Fails until the agent has written fixed.txt; prints a recognised check line.
TOY_GATE = (
    'if [ -f fixed.txt ]; then echo "gate-check style pass"; '
    'else echo "style: feature.py:1 missing docstring"; echo "gate-check style fail"; exit 1; fi'
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
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.prompts)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "done"}],
        }
        await environment.exec(
            f"echo 'x = {len(cls.prompts)}' >> {write} && "
            f"echo {shlex.quote(json.dumps(trajectory))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 100, 10, 0.01


@pytest.fixture(autouse=True)
def _reset_script() -> None:
    ScriptedInnerAgent.prompts.clear()
    ScriptedInnerAgent.fixes = True
    ScriptedInnerAgent.fail = False


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
        inner_agent={"name": INNER, "kwargs": options.pop("inner_kwargs", {})},
        work_dir=str(tmp_path / "gate-work"),
        **{"command": TOY_GATE, **options},
    )
    return agent, LocalEnvironment(workspace, shims)


def _run(agent: GuardrailGateAgent, environment: LocalEnvironment) -> AgentContext:
    context = AgentContext()
    asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))
    return context


def _git(workspace: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True)
    return done.stdout


def _use() -> Any:
    return compile_uses(use("guardrail-gate@1", command="make check")).uses[0].context


def test_refused_commit_starts_a_fix_session_with_the_exact_gate_output(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(tmp_path, shims)

    context = _run(agent, environment)

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

    context = _run(agent, environment)

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

    gate = _run(agent, environment).metadata["guardrail_gate"]  # type: ignore[index]

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

    gate = _run(agent, environment).metadata["guardrail_gate"]  # type: ignore[index]

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


def test_the_same_wrapper_runs_on_codex_and_claude_code() -> None:
    gate_use = use("guardrail-gate@1", command="make check", max_rounds=2)
    codex = compile_uses(gate_use, harness=Harness.CODEX_CLI)
    claude = compile_uses(gate_use, harness=Harness.CLAUDE_CODE)

    assert codex.agent_wrapper == claude.agent_wrapper
    assert codex.agent_wrapper is not None
    assert dict(codex.agent_wrapper.kwargs) == {
        "command": "make check",
        "mode": "commit",
        "max_rounds": 2,
        "timeout_sec": 600,
        "component": "guardrail-gate@1",
    }
    assert not codex.native_config, "no harness hooks"
    assert not claude.native_config, "no harness hooks"
    applied = claude.apply_to_agent_config(AgentConfig(name="claude-code"))
    assert applied.import_path == GUARDRAIL_GATE_IMPORT_PATH
    assert applied.kwargs["inner_agent"] == {"name": "claude-code", "kwargs": {}}


@pytest.mark.parametrize(
    ("with_", "message"),
    [
        ({}, "missing required 'with' keys ['command']"),
        ({"command": " "}, "non-empty string"),
        ({"command": "pytest /tests/test_outputs.py"}, "hidden verifier path"),
        ({"command": "make check", "mode": "hook"}, "'commit' or 'command'"),
        ({"command": "make check", "max_rounds": 0}, "integer 1..5"),
        ({"command": "make check", "timeout_sec": True}, "integer 10..3600"),
        ({"command": "make check", "reviewer": "x"}, "unknown 'with' keys"),
    ],
)
def test_invalid_configuration_rejects_the_arm(with_: dict[str, Any], message: str) -> None:
    with pytest.raises(ComponentRejectedError, match=message.replace("[", r"\[")):
        compile_uses(use("guardrail-gate@1", **with_))


def test_the_gate_and_another_workflow_cannot_both_wrap_the_agent() -> None:
    with pytest.raises(ComponentRejectedError, match="more than one workflow wraps the agent"):
        compile_uses(
            use("guardrail-gate@1", command="make check"),
            use("review-loop@2", max_review_rounds=2),
        )


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
        use("guardrail-gate@1", command="bash toy-gate.sh", max_rounds=2, timeout_sec=60),
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
