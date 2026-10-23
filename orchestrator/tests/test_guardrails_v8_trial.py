"""python-guardrails@8 with guardrail-gate@4 in a real two-step trial (no LLM): the autofix step.

Iteration 4 of the guardrails experiment. In each arm's condition (r1: no
workspace copies, shadow gate; r2: copies, commit gate), both with
``autofix_before_check`` and the pack's ``guardrail-check --autofix``, a
scripted agent writes, at step 1, a module whose only problems are layout
(``ruff format``) and import order (``I001``), with a pytest test that covers
it. At the end of its session it records a digest of every file in ``/app``;
step 2's session records the same listing before doing anything, so the tree
the gate handed on is compared with the tree the agent left.

- r2 (enforced): the autofix pass runs before the commit check, fixes
  ``FORMAT`` and ``I001``, the first gate run passes, no fix session starts, and
  the module in ``/app`` is formatted (the commit holds it);
- r1 (shadow): the raw shadow check refuses ``FORMAT`` and ``I001``, the
  autofix runs on a throwaway copy whose check passes, and ``/app`` at step 2 is
  byte-identical to what the agent left; the copy is gone.

Both probes pass on @8's pin (its autofix smoke included).
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

from raidar.components.builtins import guardrails_v8
from raidar.components.gate_run import GATE_RECORD
from raidar.components.provision_v2 import trial_provisions
from raidar.components.session_chain import apply_session_wrappers, condition_session_wrappers
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
AUTOFIX = {"autofix_before_check": True, "autofix_command": guardrails_v8.AUTOFIX_COMMAND}
GATES = {
    "r1": {"command": "guardrail-check", "mode": "shadow", "max_rounds": 1, "timeout_sec": 240},
    "r2": {"command": "guardrail-check", "mode": "commit", "max_rounds": 1, "timeout_sec": 240},
}
STEPS = {
    "checkpoint_1": "step one: write the module and its tests",
    "checkpoint_2": "step two: nothing more to do",
}
MODULE = (
    '"""Small helpers."""\n\n'
    "import sys\nimport os\n\n\n"
    "def add(a: int,b: int) -> int :\n"
    '    """Return the sum."""\n'
    "    return  a+b\n\n\n"
    "def where() -> str:\n"
    '    """Return the separator and the platform."""\n'
    "    return os.sep + sys.platform\n"
)
TEST = (
    "from calc import add, where\n\n\n"
    "def test_add() -> None:\n    assert add(1, 2) == 3\n\n\n"
    "def test_where() -> None:\n    assert where()\n"
)
LISTING = (
    "find /app -path /app/.git -prune -o -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum"
)


class AutofixInner(BaseAgent):
    """Step 1: write the module (layout and import order only) and its test; record /app."""

    @staticmethod
    def name() -> str:
        return "autofix-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        logs = self.environment_logs_dir
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}}
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": "s1",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "Done."}],
        }
        if "step one" in instruction:
            work = (
                f"cd /app && printf '%s' {shlex.quote(MODULE)} > calc.py && "
                f"printf '%s' {shlex.quote(TEST)} > test_calc.py && "
                f"{LISTING} > {logs}/at-session-end.txt"
            )
        else:
            work = f"{LISTING} > {logs}/at-step-start.txt"
        await environment.exec(
            f"{work} ; echo {shlex.quote(json.dumps(event))} > {logs}/codex.txt && "
            f"echo {shlex.quote(json.dumps(trajectory))} > {logs}/trajectory.json"
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        context.n_input_tokens, context.n_output_tokens = 10, 1
        context.n_cache_tokens, context.cost_usd = 0, 0.0


INNER = "tests.test_guardrails_v8_trial:AutofixInner"


def _listing(agent_dir: Path, name: str) -> str:
    (path,) = list(agent_dir.rglob(name))
    return path.read_text()


@docker_trial
@pytest.mark.parametrize("arm", ["r1", "r2"])
def test_autofix_runs_before_the_commit_check_or_on_a_copy_in_shadow(
    tmp_path: Path, arm: str
) -> None:
    compiled = compile_uses(
        use("python-guardrails@8", workspace_configs=arm == "r2"),
        use("guardrail-gate@4", **GATES[arm], **AUTOFIX),
    )
    task = tmp_path / f"guardrails-v8-{arm}"
    build_task(task, compiled, STEPS)
    assert compiled.agent_wrapper is not None
    base = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )
    config = apply_session_wrappers(base, condition_session_wrappers(compiled))
    trial = run_trial_sync(task, tmp_path / "trials", config, task.name, trial_provisions(compiled))
    first = trial / "steps" / "checkpoint_1" / "agent"
    second = trial / "steps" / "checkpoint_2" / "agent"

    for index, agent_dir in enumerate((first, second)):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        assert guardrails_v8.PINNED_VERSION in json.dumps(report.to_json())
    record = json.loads(next(first.rglob(GATE_RECORD)).read_text())
    (fix,) = record["autofix"]["runs"]
    assert fix["error_type"] is None, fix
    assert {"FORMAT", "I001"} <= set(fix["codes_fixed"]), fix
    assert fix["files_changed"] == ["calc.py"]
    ended, handed_on = _listing(first, "at-session-end.txt"), _listing(second, "at-step-start.txt")
    if arm == "r2":
        assert fix["mode"] == "apply"
        (run,) = record["gate_runs"]
        assert run["passed"] is True, run
        assert [stage["stage"] for stage in record["stages"]] == ["work"]
        assert ended != handed_on, "the commit holds the fixed module"
    else:
        assert fix["mode"] == "dry_run"
        (raw,) = record["gate_runs"]
        assert raw["passed"] is False
        assert fix["post_check"]["passed"] is True, fix["post_check"]
        assert ended == handed_on, "the shadow autofix never touches /app"
