"""Iteration 3b of the guardrails experiment in a real two-step trial (no LLM).

The r2 condition with the shared session wrappers (``continue-on-stall@2``,
``transient-retry@1``), ``python-guardrails@4`` with workspace copies and
``guardrail-gate@3`` in commit mode, around a scripted agent, in the pack image
on OrbStack:

- step 1: the work session is dropped by the provider (``ApiOverloadedError``)
  and retried; the retry writes the ``smoke-03`` r2 layout (an extensionless
  ``forge`` script and a ``forge.py`` symlink to it), which @4's gate checks
  and refuses (unused import, no tests). The first fix session changes nothing
  and asks a question: it is recorded but not continued (only a step's first
  session is eligible). The second fix adds a test and the gate passes;
- step 2: the work session changes nothing and refuses ("I can't ... without
  that context"), is continued, and the gate passes.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, ClassVar

import pytest
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import ApiOverloadedError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from raidar.components.builtins.guardrails_v4 import PINNED_VERSION
from raidar.components.continue_on_stall import STALL_RECORD
from raidar.components.gate_run import GATE_RECORD
from raidar.components.integrity import condition_step_flags
from raidar.components.provision_v2 import trial_provisions
from raidar.components.session_chain import apply_session_wrappers, condition_session_wrappers
from raidar.components.transient_retry import RETRY_DIR, RETRY_RECORD, TransientRetryAgent
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    build_task,
    docker_target_ready,
    run_trial_sync,
)

docker_trial = pytest.mark.skipif(
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)
GATE = {"command": "guardrail-check", "max_rounds": 2, "timeout_sec": 240}
STEPS = {"checkpoint_1": "step one: write the tool", "checkpoint_2": "step two: extend it"}
SCRIPT = (
    "#!/usr/bin/env python3\n"
    '"""A command-line tool."""\n\n'
    "import sys\n\n\n"
    "def main() -> int:\n"
    '    """Print the argument count."""\n'
    "    print(len(sys.argv) - 1)\n"
    "    return 0\n\n\n"
    'if __name__ == "__main__":\n'
    "    raise SystemExit(main())\n"
)
TEST = (
    "import subprocess\nimport sys\nimport unittest\n\n\n"
    "class ToolTest(unittest.TestCase):\n"
    "    def test_counts(self) -> None:\n"
    "        done = subprocess.run(\n"
    '            [sys.executable, "forge", "a", "b"],\n'
    "            capture_output=True,\n"
    "            text=True,\n"
    "            check=True,\n"
    "            timeout=60,\n"
    "        )\n"
    '        self.assertEqual(done.stdout.strip(), "2")\n'
)
REFUSAL = "The storage format isn’t defined. I can’t extend `forge` without that context."
RAISE = "raise"


def _write(path: str, text: str) -> str:
    return f"mkdir -p $(dirname {path}) && printf '%s' {shlex.quote(text)} > {path}"


class ScriptedInner(BaseAgent):
    """Each session runs the next scripted (files command, final message) of its step."""

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
            "steps": [{"step_id": 1, "source": "agent", "message": message or "-"}],
        }
        logs = self.environment_logs_dir
        files = "true" if command == RAISE else command
        await environment.exec(
            f"cd /app && {files} && "
            f"echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )
        if command == RAISE:
            raise ApiOverloadedError("Selected model is at capacity. Please try a different model.")

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.0


INNER = "tests.test_guardrails_v4_trial:ScriptedInner"


def _records(agent_dir: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(agent_dir.rglob(name))]


@docker_trial
def test_r2_retries_a_dropped_session_checks_the_script_and_continues_only_the_first_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_wait(_: TransientRetryAgent, seconds: float) -> None:
        del seconds

    monkeypatch.setattr(TransientRetryAgent, "_sleep", no_wait)
    ScriptedInner.prompts.clear()
    ScriptedInner.script = {
        "checkpoint_1": [
            (RAISE, ""),
            (
                _write("forge", SCRIPT.replace("import sys\n", "import os\nimport sys\n"))
                + " && chmod +x forge && ln -sf forge forge.py",
                "Done.",
            ),
            ("true", "Should I keep the os import for later use?"),
            (
                _write("forge", SCRIPT) + " && " + _write("tests/unit/test_forge.py", TEST),
                "Removed the import and added a test.",
            ),
        ],
        "checkpoint_2": [
            ("true", REFUSAL),
            (_write("ASSUMPTIONS.md", "plain text storage\n"), "Proceeding."),
        ],
    }
    compiled = compile_uses(
        use("continue-on-stall@2"),
        use("transient-retry@1"),
        use("python-guardrails@4", workspace_configs=True),
        use("guardrail-gate@3", mode="commit", **GATE),
    )
    task = tmp_path / "guardrails-v4"
    build_task(task, compiled, STEPS)
    assert compiled.agent_wrapper is not None
    base = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )
    config = apply_session_wrappers(base, condition_session_wrappers(compiled))
    trial = run_trial_sync(task, tmp_path / "trials", config, task.name, trial_provisions(compiled))
    steps = [trial / "steps" / step / "agent" for step in STEPS]

    for index, agent_dir in enumerate(steps):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        assert PINNED_VERSION in json.dumps(report.to_json())
        assert condition_step_flags(compiled, agent_dir, index) == ()

    first = json.loads((steps[0] / "guardrail-gate" / GATE_RECORD).read_text())
    assert [run["passed"] for run in first["gate_runs"]] == [False, False, True]
    assert first["termination_reason"] == "gate_clean"
    reports = sorted((steps[0] / "guardrail-gate").glob("gate_run_*/gate-*.json"))
    refused = json.loads(reports[0].read_text())
    assert refused["files_checked"] == 1
    assert sorted(refused["counts"]["block"]) == ["F401", "TEST.missing"]
    assert {f["relpath"] for f in refused["findings"] if f["code"] == "F401"} == {"forge"}
    (work_retry, *_) = _records(steps[0] / "guardrail-gate" / "stage_1_work", RETRY_RECORD)
    assert (work_retry["retries"], work_retry["recovered"]) == (1, True)
    assert work_retry["attempts"][0]["error_class"] == "ApiOverloadedError"
    work = steps[0] / "guardrail-gate" / "stage_1_work"
    assert (work / RETRY_DIR / "attempt_1" / "codex.txt").is_file()
    stalls = _records(steps[0], STALL_RECORD)
    assert [(s["eligible"], s["stalled"], s["would_stall"]) for s in stalls] == [
        (True, False, False),
        (False, False, True),
        (False, False, False),
    ]
    assert first["stages"][0]["n_input_tokens"] == 20, "both attempts are billed"

    (stall,) = _records(steps[1], STALL_RECORD)
    assert (stall["eligible"], stall["stalled"], stall["continued"]) == (True, True, True)
    assert "cannot-without" in stall["session"]["matched"]
    second = json.loads((steps[1] / "guardrail-gate" / GATE_RECORD).read_text())
    assert [run["passed"] for run in second["gate_runs"]] == [True]
    assert "cut off by the model provider" in ScriptedInner.prompts[1]
    assert "could not\nproceed without more information" in ScriptedInner.prompts[-1]
