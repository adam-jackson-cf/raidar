"""python-guardrails@5 in a real one-step trial (no LLM): the agent can run the pack's tools.

smoke-04 of the guardrails experiment (O1): the gate demanded tests, coverage
and formatting, but ``pytest``, ``coverage``, ``ruff`` and ``mypy`` were not on
the agent's PATH in either arm, so the agent could check only by running
``guardrail-check`` again and again. In both conditions (r1: no workspace
config copies, shadow gate; r2: copies, commit gate), a scripted agent writes a
module and its test in ``/app`` and runs ``pytest``, ``coverage run -m pytest``,
``ruff format --check`` and ``mypy --version`` there; every one answers with
the pack's pinned version and passes, and the probe passes on @5's pin.
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

from raidar.components.catalog.guardrails_v5 import PINNED_VERSION
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
GATE = {"command": "guardrail-check", "max_rounds": 1, "timeout_sec": 240}
STEPS = {"checkpoint_1": "step one: write the module"}
MODULE = (
    '"""Add numbers."""\n\n\n'
    "def add(a: int, b: int) -> int:\n"
    '    """Return the sum."""\n'
    "    return a + b\n"
)
TEST = (
    '"""Tests for calc."""\n\nimport unittest\n\nfrom calc import add\n\n\n'
    "class AddTest(unittest.TestCase):\n"
    '    """add."""\n\n'
    "    def test_add(self) -> None:\n"
    '        """Two and three make five."""\n'
    "        self.assertEqual(add(2, 3), 5)\n"
)
TOOLS = "tools.txt"
TOOL_RUNS = (
    'echo "pytest_path=$(command -v pytest)"',
    'pytest -q -p no:cacheprovider >/tmp/pytest.out 2>&1; echo "pytest_rc=$?"',
    'echo "pytest_version=$(pytest --version 2>&1 | head -n 1)"',
    "coverage run --data-file=/tmp/.coverage -m pytest -q -p no:cacheprovider "
    '>/tmp/coverage.out 2>&1; echo "coverage_rc=$?"',
    'echo "coverage_version=$(coverage --version 2>&1 | head -n 1)"',
    'ruff format --check --no-cache . >/tmp/ruff.out 2>&1; echo "ruff_format_rc=$?"',
    'echo "ruff_version=$(ruff --version 2>&1 | head -n 1)"',
    'echo "mypy_version=$(mypy --version 2>&1 | head -n 1)"',
)


def _write(path: str, text: str) -> str:
    return f"printf '%s' {shlex.quote(text)} > {path}"


class ToolRunningInner(BaseAgent):
    """One session: write a module and its test, then run the pack's tools on them."""

    @staticmethod
    def name() -> str:
        return "tool-running-inner"

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
        runs = "; ".join(TOOL_RUNS)
        await environment.exec(
            f"cd /app && {_write('calc.py', MODULE)} && {_write('test_calc.py', TEST)} && "
            f"{{ {runs}; }} > {logs}/{TOOLS} 2>&1; "
            f"echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.0


INNER = "tests.test_guardrails_v5_trial:ToolRunningInner"


def _values(agent_dir: Path) -> dict[str, str]:
    (path,) = sorted(agent_dir.rglob(TOOLS))
    lines = [line for line in path.read_text().splitlines() if "=" in line]
    return dict(line.split("=", 1) for line in lines)


@docker_trial
@pytest.mark.parametrize(
    ("workspace_configs", "mode"), [(False, "shadow"), (True, "commit")], ids=["r1", "r2"]
)
def test_the_agent_runs_the_packs_pinned_tools_in_both_conditions(
    tmp_path: Path, workspace_configs: bool, mode: str
) -> None:
    compiled = compile_uses(
        use("python-guardrails@5", workspace_configs=workspace_configs),
        use("guardrail-gate@3", mode=mode, **GATE),
    )
    task = tmp_path / "guardrails-v5"
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
    assert PINNED_VERSION in json.dumps(report.to_json())
    assert condition_step_flags(compiled, agent_dir, 0) == ()
    values = _values(agent_dir)
    assert values["pytest_path"] == "/usr/local/bin/pytest"
    assert (values["pytest_rc"], values["coverage_rc"], values["ruff_format_rc"]) == (
        "0",
        "0",
        "0",
    ), values
    assert values["pytest_version"] == "pytest 9.1.1"
    assert values["coverage_version"].startswith("Coverage.py, version 7.16.2")
    assert values["ruff_version"] == "ruff 0.8.6"
    assert values["mypy_version"].startswith("mypy 2.3.1")
