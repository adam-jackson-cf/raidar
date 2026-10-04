"""python-guardrails@1 in the pack image on OrbStack (no LLM).

- A real two-step Harbor trial: python-guardrails@1 with guardrail-gate@1 running
  the experiment's own hook command, around a scripted inner agent that writes
  a refused file, edits the workspace ``ruff.toml`` copy and runs
  ``git commit --no-verify``, then fixes the file when the gate refuses. It
  proves the install, the per-step probe, the commit refusal with the pinned
  tools, the per-run JSON reports and the integrity flags end to end.
- The rule-coverage verification (``make guardrails-verify``; opt-in with
  ``RAIDAR_GUARDRAILS_VERIFY=1`` because it runs 146 canary gate runs): the
  skill's validator over the approved ledger, executing every canary against
  the installed pack in the image, the parity canary through guardrail-gate@1's
  rendered commit script. Exactly the three deferred rules may fail.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from raidar.components.builtins.guardrails import PINNED_VERSION, install_script
from raidar.components.gate_run import GATE_RECORD
from raidar.components.integrity import condition_step_flags
from raidar.components.provision import condition_provisions
from raidar.packs.python import image_tag, load_pack_spec
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    DOCKER_CONTEXT,
    build_task,
    orbstack_available,
    pack_image_present,
    run_trial_sync,
)
from tests.python_guardrails_support import assemble_verify_root, experiment_gate_use

INNER = "tests.test_component_python_guardrails_trial:TamperingInnerAgent"
DEFERRED = ("PRETTIER.version", "SECURITY.dependency-audit", "TEST.coverage-minimum")
VERIFY_SLUG = "00-python-guardrails"

docker_trial = pytest.mark.skipif(
    not (orbstack_available() and pack_image_present()),
    reason="needs OrbStack (external data dir) and the built python pack image",
)


class TamperingInnerAgent(BaseAgent):
    """Step 1 work: a refused file, an edited config copy and a --no-verify commit.

    A fix session (its prompt carries the gate's output) rewrites the file so the
    gate passes; step 2's work session writes nothing.
    """

    sessions: ClassVar[list[str]] = []

    @staticmethod
    def name() -> str:
        return "tampering-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        cls = type(self)
        fixing = "The gate's output:" in instruction
        first = not cls.sessions
        cls.sessions.append("fix" if fixing else "work")
        if fixing:
            command = "printf 'print(1)\\n' > bad.py"
        elif first:
            # The overlay is not the agent's to write in place (uploaded read-only), so
            # the edit replaces the file, as an agent's rewrite would.
            command = (
                "printf 'import os\\n' > bad.py && "
                "{ cat ruff.toml; printf 'extend-ignore = [\"F401\"]\\n'; } > /tmp/ruff.new && "
                "rm -f ruff.toml && mv /tmp/ruff.new ruff.toml && "
                "{ git commit --no-verify -q -m wip 2>/dev/null || true; }"
            )
        else:
            command = "true"
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.sessions)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "message": "done",
                    "tool_calls": [
                        {
                            "tool_call_id": "c1",
                            "function_name": "exec_command",
                            "arguments": {"cmd": command},
                        }
                    ],
                }
            ],
        }
        await environment.exec(
            f"{command}; echo {shlex.quote(json.dumps(trajectory))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 10, 1, 0.0


@docker_trial
def test_gate_refuses_with_the_pinned_pack_and_tampering_is_flagged_in_a_real_trial(
    tmp_path: Path,
) -> None:
    TamperingInnerAgent.sessions.clear()
    gate = {**experiment_gate_use(), "max_rounds": 1, "timeout_sec": 120}
    compiled = compile_uses(use("python-guardrails@1"), use("guardrail-gate@1", **gate))
    task = tmp_path / "python-guardrails"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})
    assert compiled.agent_wrapper is not None
    agent = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )

    trial = run_trial_sync(
        task, tmp_path / "trials", agent, task.name, condition_provisions(compiled)
    )

    steps = [trial / "steps" / step / "agent" for step in ("checkpoint_1", "checkpoint_2")]
    for index, agent_dir in enumerate(steps):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        assert PINNED_VERSION in json.dumps(report.to_json())
    first = json.loads((steps[0] / "guardrail-gate" / GATE_RECORD).read_text())
    refused, passed = first["gate_runs"]
    assert (refused["passed"], refused["committed"], refused["exit_code"]) == (False, False, 1)
    assert "gate-check ruff fail" in (steps[0] / refused["log"]).read_text()
    assert (passed["passed"], first["termination_reason"]) == (True, "gate_clean")
    report_one = json.loads(next((steps[0] / "guardrail-gate").rglob("gate-*.json")).read_text())
    assert report_one["counts"]["block"] == {"F401": 1}, "the edited copy did not loosen F401"
    assert report_one["workspace_configs"]["ruff.toml"]["state"] == "modified"

    flags = condition_step_flags(compiled, steps[0], 0)
    assert any("workspace-config-modified: ruff.toml at guardrail-gate/" in f for f in flags)
    assert any("hook-bypass-attempt: git-no-verify" in f for f in flags)
    later = condition_step_flags(compiled, steps[1], 1)
    assert any("ruff.toml at step start (modified)" in f for f in later)
    assert not any("hook-bypass-attempt" in f for f in later)
    assert TamperingInnerAgent.sessions == ["work", "fix", "work"]


# ---- rule-coverage verification (opt-in) -------------------------------------------

coverage_verification = pytest.mark.skipif(
    os.environ.get("RAIDAR_GUARDRAILS_VERIFY") != "1"
    or not (orbstack_available() and pack_image_present()),
    reason="opt-in (make guardrails-verify): OrbStack, the pack image and PyPI egress",
)

# The lockfile-parity canary's project; its uv.lock is made in the image by the gate's uv.
DEMO_PYPROJECT = (
    '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = []\n'
)
VERIFY_SCRIPT = """
set -euo pipefail
mkdir -p /tmp/raidar-components/{slug} && cp -R /verify/pack /tmp/raidar-components/{slug}/
bash /verify/install.sh >/dev/null
chown -R agent:agent /verify
su agent -s /bin/bash -c '
set -euo pipefail
demo="$(mktemp -d)" && cp /verify/demo-pyproject.toml "$demo/pyproject.toml" && cd "$demo"
uv lock --offline --quiet && cp uv.lock /verify/demo-uv.lock
cd /verify
python3 make_fixtures.py canaries/fixtures demo-uv.lock specs.json
python3 validate_rule_coverage.py --root /verify --catalog enaible-rules.json \\
  --ledger coverage-ledger.json > validator.out 2>&1 || echo "validator_rc=$?" >> validator.out
# Negative control: a copy of the pack with three checkers disabled must fail their canaries.
cp -R /opt/raidar-tools/python-guardrails /tmp/mutant
sed -i "s/^off = \\[/off = [\\n    \\"F401\\",/" /tmp/mutant/config/policy.toml
sed -i "s/\\"C901\\", //" /tmp/mutant/config/ruff.toml
sed -i "s/^CLASS_MAX_LINES = 700/CLASS_MAX_LINES = 100000/" \\
  /tmp/mutant/lib/guardrails/source_rules.py
GUARDRAILS_HOME=/tmp/mutant python3 validate_rule_coverage.py --root /verify \\
  --catalog enaible-rules.json --ledger mutant-ledger.json > mutant.out 2>&1 || true
'
"""
MUTATED = ("PYQ200", "RUFF.SELECT.C901", "RUFF.SELECT.F")


@coverage_verification
def test_every_applicable_rule_is_proven_by_its_canaries_in_the_pack_image(
    tmp_path: Path,
) -> None:
    root = assemble_verify_root(tmp_path / "verify")
    (root / "install.sh").write_text(install_script(VERIFY_SLUG))
    (root / "run.sh").write_text(VERIFY_SCRIPT.format(slug=VERIFY_SLUG))
    ledger = json.loads((root / "coverage-ledger.json").read_text())
    ledger["rules"] = [row for row in ledger["rules"] if row["id"] in MUTATED]
    (root / "mutant-ledger.json").write_text(json.dumps(ledger))
    (root / "demo-pyproject.toml").write_text(DEMO_PYPROJECT)
    name = f"raidar-guardrails-verify-{uuid.uuid4().hex[:8]}"

    done = subprocess.run(
        ["docker", "--context", DOCKER_CONTEXT, "run", "--rm", "--name", name]
        + ["-v", f"{root}:/verify", "--user", "root", image_tag(load_pack_spec())]
        + ["bash", "/verify/run.sh"],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )

    output = (root / "validator.out").read_text() if (root / "validator.out").exists() else ""
    print(output)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (root / "specs.json").read_text() == (
        Path(__file__).parents[1] / "tests/fixtures/python_guardrails/specs.json"
    ).read_text(), "the image's fixture generator wrote different canary specs"
    failures = sorted(line for line in output.splitlines() if line.startswith("rule coverage"))
    assert failures == [f"rule coverage FAIL: {rule}: rule is not implemented" for rule in DEFERRED]
    mutant = (root / "mutant.out").read_text()
    for rule in MUTATED:
        assert f"rule coverage FAIL: {rule}: violating canary passed" in mutant, mutant[-2000:]
