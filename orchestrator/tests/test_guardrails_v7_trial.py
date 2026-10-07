"""python-guardrails@7 in a real one-step trial (no LLM): ``python -m pytest`` works in both arms.

smoke-3b-3 of the guardrails experiment (I2): every ``python -m pytest`` the agent
ran failed with "No module named pytest", because @5/@6 put the pack's pytest on
the PATH as an executable but not on the image interpreter's import path. In
each arm's condition (r1: no workspace copies, shadow gate; r2: copies, commit
gate) a scripted agent writes a module and its pytest-style test under
``tests/unit`` and runs ``python -m pytest``, ``python3 -m pytest`` and
``python3 -m coverage run -m pytest`` in ``/app`` as the agent user. Under @6
they fail with "No module named pytest" (or ``coverage``); under @7 they pass with the pinned
pytest, ``import mypy`` still fails, the probe passes on @7's pin and the step
raises no integrity flag.
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

from raidar.components.builtins import guardrails_v6, guardrails_v7
from raidar.components.integrity import condition_step_flags
from raidar.components.provision_v2 import trial_provisions
from raidar.components.session_chain import apply_session_wrappers, condition_session_wrappers
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
GATES = {
    "r1": {"command": "guardrail-check", "mode": "shadow", "max_rounds": 1, "timeout_sec": 240},
    "r2": {"command": "guardrail-check", "mode": "commit", "max_rounds": 1, "timeout_sec": 240},
}
STEPS = {"checkpoint_1": "step one: write the module and its tests"}
MODULE = (
    '"""Add numbers."""\n\n\n'
    "def add(a: int, b: int) -> int:\n"
    '    """Return the sum."""\n'
    "    return a + b\n"
)
TEST_PATH = "tests/unit/test_calc.py"
TEST = "from calc import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n"
COMMANDS = {
    "python_m_pytest": "python -m pytest -q -p no:cacheprovider",
    "python3_m_pytest": "python3 -m pytest -q -p no:cacheprovider",
    "python3_m_coverage": "python3 -m coverage run -m pytest -q -p no:cacheprovider",
    "import_mypy": "python3 -c 'import mypy'",
}
PINS = {6: guardrails_v6.PINNED_VERSION, 7: guardrails_v7.PINNED_VERSION}
RESULTS = "python-m-results.txt"


def _write(path: str, text: str) -> str:
    folder = shlex.quote(str(Path(path).parent))
    return f"mkdir -p {folder} && printf '%s' {shlex.quote(text)} > {path}"


def _runs(logs: str) -> str:
    """Run each command in /app, appending ``name=<rc>`` and its last output line."""
    lines = []
    for name, command in COMMANDS.items():
        lines.append(
            f"out=$({command} 2>&1); rc=$?; "
            f'printf "%s=%s %s\\n" {name} "$rc" "$(printf "%s" "$out" | tail -n 1)" '
            f">> {logs}/{RESULTS}"
        )
    return " ; ".join(lines)


class PythonModuleInner(BaseAgent):
    """Every session: write the module and its test, then run the tools as modules."""

    @staticmethod
    def name() -> str:
        return "python-module-inner"

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
            f"rm -f {logs}/{RESULTS} && {_runs(str(logs))} ; rm -rf .coverage && "
            f"echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.0


INNER = "tests.test_guardrails_v7_trial:PythonModuleInner"


def _results(agent_dir: Path) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(agent_dir.rglob(RESULTS)):
        for line in path.read_text().splitlines():
            name, _, rest = line.partition("=")
            rc, _, tail = rest.partition(" ")
            found[name] = (rc, tail)
    return found


@docker_trial
@pytest.mark.parametrize(("version", "arm"), [(6, "r2"), (7, "r1"), (7, "r2")])
def test_python_m_pytest_fails_under_v6_and_runs_under_v7(
    tmp_path: Path, version: int, arm: str
) -> None:
    compiled = compile_uses(
        use(f"python-guardrails@{version}", workspace_configs=arm == "r2"),
        use("guardrail-gate@3", **GATES[arm]),
    )
    task = tmp_path / f"guardrails-v{version}-{arm}"
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
    results = _results(agent_dir)
    assert set(results) == set(COMMANDS), results
    assert results["import_mypy"][0] == "1", results
    modules = ("python_m_pytest", "python3_m_pytest", "python3_m_coverage")
    if version == 6:
        for name in modules:
            assert results[name][0] == "1", results
            assert "No module named" in results[name][1], results
    else:
        for name in modules:
            assert results[name][0] == "0", results
            assert "1 passed" in results[name][1], results
        assert condition_step_flags(compiled, agent_dir, 0) == ()
