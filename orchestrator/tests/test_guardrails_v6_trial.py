"""python-guardrails@6 in a real one-step trial (no LLM): a broad test class is not refused.

smoke-3b-2 of the guardrails experiment (O1): the gate refused
``tests/unit/test_forge.py`` for ``PLR0904`` (too many public methods) once the
agent's ``unittest`` suite passed 20 tests in one class, and the fix sessions
reshaped the tests instead of the code. In r2's condition (workspace copies,
commit gate) a scripted agent writes a module and a 22-method ``TestCase`` for
it under ``tests/unit``: @5's first gate run reports ``PLR0904`` at the test
file, @6's does not; under @6 the probe passes on its pin and the step raises no
integrity flag (its unmodified ``ruff.toml`` copy is compared with @6's bytes).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from raidar.components.catalog import guardrails_v5, guardrails_v6
from raidar.components.definition.integrity import condition_step_flags
from raidar.components.runtime.provision_v2 import trial_provisions
from raidar.components.runtime.session_chain import (
    apply_session_wrappers,
    condition_session_wrappers,
)
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
GATE = {"command": "guardrail-check", "mode": "commit", "max_rounds": 1, "timeout_sec": 240}
STEPS = {"checkpoint_1": "step one: write the module and its tests"}
MODULE = (
    '"""Add numbers."""\n\n\n'
    "def add(a: int, b: int) -> int:\n"
    '    """Return the sum."""\n'
    "    return a + b\n"
)
TEST_PATH = "tests/unit/test_calc.py"
TEST = (
    "import unittest\n\nfrom calc import add\n\n\n"
    "class AddTest(unittest.TestCase):\n"
    + "\n".join(
        f"    def test_add_{i}(self) -> None:\n        self.assertEqual(add({i}, 1), {i + 1})\n"
        for i in range(22)
    )
)
PINS = {5: guardrails_v5.PINNED_VERSION, 6: guardrails_v6.PINNED_VERSION}


def _write(path: str, text: str) -> str:
    folder = shlex.quote(str(Path(path).parent))
    return f"mkdir -p {folder} && printf '%s' {shlex.quote(text)} > {path}"


class BroadSuiteInner(BaseAgent):
    """Every session: write the module and its 22-test class (the fix session changes nothing)."""

    @staticmethod
    def name() -> str:
        return "broad-suite-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        del instruction
        logs = self.environment_logs_dir
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}}
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": "s1",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "Done."}],
        }
        await environment.exec(
            f"cd /app && {_write('calc.py', MODULE)} && {_write(TEST_PATH, TEST)} && "
            f"echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.0


INNER = "tests.test_guardrails_v6_trial:BroadSuiteInner"


def _first_run_findings(agent_dir: Path) -> list[dict[str, object]]:
    (report,) = sorted((agent_dir / "guardrail-gate" / "gate_run_1").glob("gate-*.json"))
    findings = json.loads(report.read_text()).get("findings") or []
    return [finding for finding in findings if isinstance(finding, dict)]


@docker_trial
@pytest.mark.parametrize("version", [5, 6])
def test_a_broad_test_class_is_refused_by_v5_and_not_by_v6(tmp_path: Path, version: int) -> None:
    compiled = compile_uses(
        use(f"python-guardrails@{version}", workspace_configs=True),
        use("guardrail-gate@3", **GATE),
    )
    task = tmp_path / f"guardrails-v{version}"
    build_task(task, compiled, STEPS)
    assert compiled.agent_wrapper is not None
    base = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )
    config = apply_session_wrappers(base, condition_session_wrappers(compiled))
    trial = run_trial_sync(task, tmp_path / "trials", config, task.name, trial_provisions(compiled))
    agent_dir = trial / "steps" / "checkpoint_1" / "agent"

    report = compiled.probe_step(agent_dir, 0)
    assert report.passed, report.to_json()
    assert PINS[version] in json.dumps(report.to_json())
    public_methods = [
        finding for finding in _first_run_findings(agent_dir) if finding.get("code") == "PLR0904"
    ]
    if version == 5:
        assert [finding.get("relpath") for finding in public_methods] == [TEST_PATH]
    else:
        assert public_methods == []
        assert condition_step_flags(compiled, agent_dir, 0) == ()
