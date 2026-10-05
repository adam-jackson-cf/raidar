"""python-guardrails@3, guardrail-gate@3 and continue-on-stall@1 in real two-step trials (no LLM).

Scripted inner agents act like a coding agent (files in ``/app``, a Codex
``codex.txt`` final message, a trajectory) so the real Harbor trial, the pack
installed at environment start, the commit gate running the agent's own tests,
the pre-fix snapshots and the stall continuation all run end to end in the
pack image on OrbStack:

- the r2 condition (``continue-on-stall@1`` shared, ``python-guardrails@3``
  with workspace copies, ``guardrail-gate@3`` in commit mode): step 1 is
  refused for missing tests, then for coverage, then passes, with a pre-fix
  snapshot before each fix; step 2's work session stalls on a question and is
  continued before the gate runs;
- the r1 condition (the same pack without copies, the gate in shadow mode):
  the shadow gate runs the agent's tests in a throwaway copy, so nothing they
  write reaches ``/app``, and the agent never sees the gate.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, ClassVar

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from raidar.components.builtins.guardrails_v3 import PINNED_VERSION
from raidar.components.continue_on_stall import STALL_DIR, STALL_RECORD
from raidar.components.gate_run import GATE_RECORD
from raidar.components.integrity import condition_step_flags
from raidar.components.provision_v2 import INSTALL_PHASE, trial_provisions
from raidar.components.session_chain import apply_session_wrappers, condition_session_wrappers
from raidar.oracle.snapshot import tree_digest
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    build_task,
    orbstack_available,
    pack_image_present,
    run_trial_sync,
)

docker_trial = pytest.mark.skipif(
    not (orbstack_available() and pack_image_present()),
    reason="needs OrbStack (external data dir) and the built python pack image",
)
GATE = {"command": "guardrail-check", "max_rounds": 2, "timeout_sec": 240}
STEPS = {"checkpoint_1": "step one: write the module", "checkpoint_2": "step two: extend it"}
MODULE = 'def double(x: int) -> int:\n    """Double x."""\n    return 2 * x\n'
TRIPLE = '\n\ndef triple(x: int) -> int:\n    """Triple x."""\n    return 3 * x\n'
QUAD = '\n\ndef quad(x: int) -> int:\n    """Quadruple x."""\n    return 4 * x\n'


def _test_file(*names: str, write: bool = False) -> str:
    factor = {"double": 2, "triple": 3, "quad": 4}
    lines = ["import unittest", "", f"from mod import {', '.join(sorted(names))}", "", ""]
    lines += ["class ModTest(unittest.TestCase):"]
    for name in names:
        lines += [f"    def test_{name}(self) -> None:"]
        if write:
            lines += ['        with open("written.txt", "w", encoding="utf-8") as out:']
            lines += ['            out.write("x")']
        lines += [f"        self.assertEqual({name}(1), {factor[name]})", ""]
    return "\n".join(lines).rstrip() + "\n"


def _write(path: str, text: str) -> str:
    return f"printf '%s' {shlex.quote(text)} > {path}"


class ScriptedInner(BaseAgent):
    """Each session runs the next scripted (files command, final message) for its step."""

    prompts: ClassVar[list[str]] = []
    script: ClassVar[dict[str, list[tuple[str, str]]]] = {}

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
        step = "checkpoint_2" if "step two" in instruction else "checkpoint_1"
        command, message = cls.script[step].pop(0)
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": message}}
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.prompts)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": message}],
        }
        logs = self.environment_logs_dir
        await environment.exec(
            f"cd /app && {command} && ls -A > {logs}/seen.txt && "
            f"echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 10, 1, 0.0


INNER = "tests.test_guardrails_v3_trial:ScriptedInner"


def _run(tmp_path: Path, *uses: Any) -> tuple[Any, list[Path]]:
    compiled = compile_uses(*uses)
    task = tmp_path / "guardrails-v3"
    build_task(task, compiled, STEPS)
    assert compiled.agent_wrapper is not None
    base = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )
    # The execution layer's nesting, with the scripted agent as the native one.
    config = apply_session_wrappers(base, condition_session_wrappers(compiled))
    trial = run_trial_sync(task, tmp_path / "trials", config, task.name, trial_provisions(compiled))
    return compiled, [trial / "steps" / step / "agent" for step in STEPS]


def _record(agent_dir: Path) -> dict[str, Any]:
    return json.loads((agent_dir / "guardrail-gate" / GATE_RECORD).read_text())


def _stall_records(agent_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(agent_dir.rglob(STALL_RECORD))]


def _gate_reports(agent_dir: Path) -> list[dict[str, Any]]:
    paths = sorted((agent_dir / "guardrail-gate").glob("gate_run_*/gate-*.json"))
    return [json.loads(path.read_text()) for path in paths]


def _provision(agent_dir: Path) -> dict[str, Any]:
    path = next((agent_dir / "raidar-components").glob("*python-guardrails.provision.jsonl"))
    return json.loads(path.read_text().splitlines()[-1])


@docker_trial
def test_r2_refuses_untested_and_uncovered_work_snapshots_each_fix_and_continues_a_stall(
    tmp_path: Path,
) -> None:
    ScriptedInner.prompts.clear()
    ScriptedInner.script = {
        "checkpoint_1": [
            (_write("mod.py", MODULE + TRIPLE), "Done."),
            (_write("test_mod.py", _test_file("double")), "Added a test."),
            (_write("test_mod.py", _test_file("double", "triple")), "Covered triple."),
        ],
        "checkpoint_2": [
            ("true", "Should quad return an int or a float? Please confirm before I proceed."),
            (
                _write("ASSUMPTIONS.md", "quad returns an int, like double\n")
                + " && "
                + f"printf '%s' {shlex.quote(QUAD)} >> mod.py && "
                + _write("test_mod.py", _test_file("double", "triple", "quad")),
                "Implemented quad.",
            ),
        ],
    }

    compiled, steps = _run(
        tmp_path,
        use("continue-on-stall@1"),
        use("python-guardrails@3", workspace_configs=True),
        use("guardrail-gate@3", mode="commit", **GATE),
    )

    for index, agent_dir in enumerate(steps):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        provision = _provision(agent_dir)
        assert provision["install"]["phase"] == INSTALL_PHASE
        values = provision["check"]["values"]
        assert values["install_version"] == PINNED_VERSION
        assert values["tree_digest"] == values["install_tree_digest"]
        assert condition_step_flags(compiled, agent_dir, index) == ()

    first = _record(steps[0])
    assert [run["passed"] for run in first["gate_runs"]] == [False, False, True]
    assert first["termination_reason"] == "gate_clean"
    blocking = [sorted(report["counts"]["block"]) for report in _gate_reports(steps[0])]
    assert blocking == [["TEST.missing"], ["TEST.coverage-minimum"], []]
    tests = [report["tests"] for report in _gate_reports(steps[0])]
    assert (tests[1]["coverage_percent"], tests[2]["coverage_percent"]) == (75.0, 100.0)
    snapshots = first["pre_fix_snapshots"]
    assert [(item["round"], item["before_session"]) for item in snapshots] == [
        (1, "stage_2_fix"),
        (2, "stage_3_fix"),
    ]
    for item, files in zip(snapshots, ({"mod.py"}, {"mod.py", "test_mod.py"}), strict=True):
        app = steps[0] / item["path"]
        assert {p.name for p in app.iterdir()} >= files
        assert ".git" not in {p.name for p in app.iterdir()}
        assert item["tree_digest"] == tree_digest(app)
    assert not any(record["stalled"] for record in _stall_records(steps[0]))

    second = _record(steps[1])
    assert [run["passed"] for run in second["gate_runs"]] == [True]
    assert second["pre_fix_snapshots"] == []
    (stall,) = _stall_records(steps[1])
    assert (stall["stalled"], stall["continued"]) == (True, True)
    assert stall["session"]["changed"] is False
    assert "question" in stall["session"]["matched"]
    assert stall["continuation"]["changed"] is True
    work = steps[1] / "guardrail-gate" / "stage_1_work"
    assert (work / STALL_DIR / "stalled_session" / "codex.txt").is_file()
    assert second["stages"][0]["n_input_tokens"] == 20, "both sessions are billed"
    root = json.loads((work / "trajectory.json").read_text())
    assert [sub["trajectory_id"] for sub in root["subagent_trajectories"]] == [
        "session_1",
        "continuation_2",
    ]
    continuation = ScriptedInner.prompts[-1]
    assert "No human is available" in continuation
    assert "ASSUMPTIONS.md" in continuation
    assert "step two" in continuation


@docker_trial
def test_r1_shadow_runs_the_agents_tests_in_a_copy_and_the_stall_wrapper_wraps_it(
    tmp_path: Path,
) -> None:
    ScriptedInner.prompts.clear()
    ScriptedInner.script = {
        "checkpoint_1": [
            (
                _write("mod.py", MODULE)
                + " && "
                + _write("test_mod.py", _test_file("double", write=True)),
                "Done.",
            ),
        ],
        "checkpoint_2": [
            ("true", "Which output format do you want?"),
            (_write("ASSUMPTIONS.md", "plain text output\n"), "Proceeding."),
        ],
    }

    compiled, steps = _run(
        tmp_path,
        use("continue-on-stall@1"),
        use("python-guardrails@3", workspace_configs=False),
        use("guardrail-gate@3", mode="shadow", **GATE),
    )

    for index, agent_dir in enumerate(steps):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        record = _record(agent_dir)
        assert (record["mode"], record["termination_reason"]) == ("shadow", "shadow_recorded")
        assert record["pre_fix_snapshots"] == []
        (gate_report,) = _gate_reports(agent_dir)
        assert gate_report["counts"]["block"] == {}, gate_report["findings"]
        assert gate_report["tests"]["tests_ran"] == 1
    seen = set((steps[1] / "guardrail-gate" / "stage_1_work" / STALL_DIR).rglob("seen.txt"))
    listing = set(next(iter(seen)).read_text().split())
    assert listing == {"mod.py", "test_mod.py"}, "the shadow test run wrote nothing to /app"
    (stall,) = _stall_records(steps[1])
    assert (stall["stalled"], stall["continued"]) == (True, True)
    assert all("gate" not in prompt.lower() for prompt in ScriptedInner.prompts)
