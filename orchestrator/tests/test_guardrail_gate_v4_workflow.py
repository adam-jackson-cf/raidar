"""guardrail-gate@4: guardrail-gate@3 with the pack's safe auto-fixers before each check.

A scripted inner agent and the local-shell environment of guardrail-gate@1's
tests run the gate's real shell scripts (the autofix script, git, the
pre-commit hook, ``git commit``). A toy autofix command stands in for the pack's
(it rewrites ``x  = 1`` as ``x = 1`` and writes the report the contract asks
for); a toy gate refuses ``x  =``. Enforced, the autofix runs before every
commit check and a failing autofix is typed and falls back to @3; in shadow
mode the workspace stays byte-identical, the agent sees nothing, and the fixed
copy's check is recorded beside the raw one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Trajectory

from raidar.components import ComponentRejectedError, StepEvidence
from raidar.components.autofix_run import (
    ERROR_TYPES,
    REPORT_MARK,
    AutofixMode,
    AutofixRun,
    read_autofix_run,
)
from raidar.components.builtins.gate_v4 import GUARDRAIL_GATE_V4, GUARDRAIL_GATE_V4_IMPORT_PATH
from raidar.components.gate_run import GATE_RECORD, SIGNAL
from raidar.components.guardrail_gate_v4 import GuardrailGateV4Agent
from raidar.components.registry import fingerprint
from tests.component_support import compile_uses, use
from tests.test_guardrail_gate_workflow import LocalEnvironment

REF = "guardrail-gate@4"
INNER = "tests.test_guardrail_gate_v4_workflow:LayoutInnerAgent"
TOY_GATE = (
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
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.prompts)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "done"}],
        }
        await environment.exec(
            f"{command} && echo {shlex.quote(json.dumps(trajectory))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 100, 10, 0.01


@pytest.fixture(autouse=True)
def _reset() -> None:
    LayoutInnerAgent.prompts.clear()


@pytest.fixture
def shims(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if subprocess.run(["bash", "-c", "command -v timeout"], capture_output=True).returncode:
        shim = bin_dir / "timeout"
        shim.write_text('#!/bin/sh\n[ "$1" = "-k" ] && shift 2\nshift\nexec "$@"\n')
        shim.chmod(0o755)
    return bin_dir


def _agent(tmp_path: Path, shims: Path, **options: Any) -> tuple[GuardrailGateV4Agent, Any]:
    workspace, logs = tmp_path / "app", tmp_path / "logs"
    workspace.mkdir(parents=True)
    logs.mkdir()
    (workspace / "main.py").write_text("print('hi')\n")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "big.bin").write_text("not for the repository\n")
    agent = GuardrailGateV4Agent(
        logs_dir=logs,
        model_name="openai/gpt-6-luna",
        environment_logs_dir=logs,  # type: ignore[arg-type]
        inner_agent={"name": INNER},
        work_dir=str(tmp_path / "gate-work"),
        **{"command": TOY_GATE, "max_rounds": 2, "timeout_sec": 60, **options},
    )
    return agent, LocalEnvironment(workspace, shims)


def _run(agent: GuardrailGateV4Agent, environment: Any) -> dict[str, Any]:
    context = AgentContext()
    asyncio.run(agent.run("Build the feature.", cast(BaseEnvironment, environment), context))
    return cast(dict[str, Any], (context.metadata or {})["guardrail_gate"])


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _use(**with_: Any) -> Any:
    return compile_uses(use(REF, command="guardrail-check", **with_)).uses[0].context


# ---- enforced: autofix, re-stage, commit ---------------------------------------------------


def test_enforced_autofix_runs_before_the_commit_so_a_layout_refusal_never_happens(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(
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
    trajectory = Trajectory.model_validate_json((tmp_path / "logs" / "trajectory.json").read_text())
    assert trajectory.agent.version == "4"
    outcome = GUARDRAIL_GATE_V4.probe(
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
    gate_cmd = f"[ -f fixed.txt ] || {{ echo 'gate-check tests fail'; exit 1; }}; {TOY_GATE}"
    agent, environment = _agent(
        tmp_path,
        shims,
        command=gate_cmd,
        autofix_before_check=True,
        autofix_command=f"touch fixed.txt.pending; {TOY_AUTOFIX}",
    )
    LayoutInnerAgent.prompts.clear()

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
def test_a_failed_autofix_is_typed_and_the_gate_falls_back_to_v3(
    tmp_path: Path, shims: Path, command: str, error_type: str
) -> None:
    agent, environment = _agent(tmp_path, shims, autofix_before_check=True, autofix_command=command)

    gate = _run(agent, environment)

    first = gate["autofix"]["runs"][0]
    assert (first["error_type"], first["fallback"], first["files_changed"]) == (
        error_type,
        True,
        None,
    )
    assert first["error"]
    assert gate["autofix"]["errors"] == len(gate["autofix"]["runs"])
    # @3's behaviour: the unformatted tree is refused and the fix session repairs it.
    assert gate["gate_runs"][0]["passed"] is False
    assert [stage["stage"] for stage in gate["stages"]] == ["work", "fix"]
    assert (gate["termination_reason"], gate[SIGNAL]) == ("gate_clean", True)
    assert error_type in ERROR_TYPES


def test_without_autofix_the_gate_is_v3(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(tmp_path, shims)

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


# ---- shadow: a throwaway copy, the workspace untouched ---------------------------------------


def test_shadow_autofix_checks_a_fixed_copy_and_leaves_the_workspace_byte_identical(
    tmp_path: Path, shims: Path
) -> None:
    agent, environment = _agent(
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
    outcome = GUARDRAIL_GATE_V4.probe(
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
    agent, environment = _agent(
        tmp_path, shims, mode="shadow", autofix_before_check=True, autofix_command=probe
    )
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path / "app", check=True)

    _run(agent, environment)

    where, venv, git = (tmp_path / "copy.txt").read_text().splitlines()
    assert where.endswith("/gate-work/autofix/app")
    assert venv == str((tmp_path / "app" / ".venv").resolve())
    assert git == "", "the copy has no .git"


def test_a_failed_shadow_autofix_is_typed_with_no_post_check(tmp_path: Path, shims: Path) -> None:
    agent, environment = _agent(
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
    agent, environment = _agent(
        tmp_path,
        shims,
        mode="shadow",
        command="./missing-gate.sh",
        autofix_before_check=True,
        autofix_command=TOY_AUTOFIX,
    )

    gate = _run(agent, environment)

    assert (gate["termination_reason"], gate["autofix"]["runs"]) == ("gate_error", [])


# ---- reading a pass back -------------------------------------------------------------------


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


# ---- the built-in ----------------------------------------------------------------------------


def test_the_builtin_passes_the_autofix_options_to_the_wrapper() -> None:
    compiled = compile_uses(
        use(
            REF,
            command="guardrail-check",
            mode="commit",
            autofix_before_check=True,
            autofix_command="guardrail-check --autofix",
        )
    )
    wrapper = compiled.agent_wrapper
    assert wrapper is not None
    assert wrapper.import_path == GUARDRAIL_GATE_V4_IMPORT_PATH
    kwargs = dict(wrapper.kwargs)
    assert (kwargs["autofix_before_check"], kwargs["autofix_command"], kwargs["component"]) == (
        True,
        "guardrail-check --autofix",
        REF,
    )
    off = compile_uses(use(REF, command="guardrail-check")).agent_wrapper
    assert off is not None
    assert "autofix_before_check" not in dict(off.kwargs)


@pytest.mark.parametrize(
    "with_",
    [
        {"autofix_command": "guardrail-check --autofix"},
        {"autofix_before_check": True},
        {"autofix_before_check": "yes", "autofix_command": "x"},
        {"autofix_before_check": True, "autofix_command": "  "},
        {"autofix_before_check": True, "autofix_command": "cat /tests/test_x.py"},
        {"mode": "command", "autofix_before_check": True, "autofix_command": "x"},
        {"autofix_timeout": 3},
    ],
)
def test_the_builtin_refuses_inconsistent_autofix_keys(with_: dict[str, Any]) -> None:
    with pytest.raises(ComponentRejectedError):
        compile_uses(use(REF, command="guardrail-check", **with_))


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

    outcome = GUARDRAIL_GATE_V4.probe(use_ctx, StepEvidence(tmp_path), 0)

    assert not outcome.passed
    assert GUARDRAIL_GATE_V4.probe(_use(), StepEvidence(tmp_path), 0).passed


def test_the_fingerprint_covers_the_autofix_modules_and_v3_stays_frozen() -> None:
    files = GUARDRAIL_GATE_V4.manifest.fingerprint
    assert "raidar/components/autofix_run.py" in files
    assert "raidar/components/guardrail_gate_v4.py" in files
    assert fingerprint(GUARDRAIL_GATE_V4) != ""
    from raidar.components.builtins.gate_v3 import GUARDRAIL_GATE_V3

    assert "raidar/components/autofix_run.py" not in GUARDRAIL_GATE_V3.manifest.fingerprint
