"""python-guardrails in the pack image on OrbStack (no LLM).

- A real two-step Harbor trial: python-guardrails with guardrail-gate running
  the experiment's own hook command, around a scripted inner agent that writes
  a refused file, edits the workspace ``ruff.toml`` copy and runs
  ``git commit --no-verify``, then fixes the file when the gate refuses. It
  proves the install, the per-step probe, the commit refusal with the pinned
  tools, the per-run JSON reports and the integrity flags end to end.
- The rule tests (``make guardrails-verify``; opt-in with
  ``RAIDAR_GUARDRAILS_VERIFY=1`` because it runs two canary gate runs per rule):
  every implemented rule's violating canary fires and its clean canary passes
  (``run_canaries.py``) against the pack installed in the image by its own
  install script, the parity canary through the experiment's guardrail-gate
  commit script; a mutant pack with checkers disabled (three, the coverage
  minimum, the scope reduced so both ``SCOPE.*`` canaries fail, and the test
  files' ``PLR0904`` ignore removed so the test-class canary's clean fixture is
  refused) must fail exactly those rules' canaries; and ``--autofix`` with the
  pinned Ruff. No file of the setup-repository-guardrails skill is involved.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, ClassVar

import pytest
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import AgentConfig

from raidar.components.catalog.guardrails import (
    PAYLOAD_DIR,
    PAYLOAD_FILES,
    PINNED_VERSION,
    install_script,
)
from raidar.components.definition.integrity import condition_step_flags
from raidar.components.runtime.gate_run import GATE_RECORD
from raidar.components.runtime.provision import trial_provisions
from raidar.packs.python.image import image_tag
from raidar.packs.python.models import load_pack_spec
from tests.component_support import compile_uses, use
from tests.component_trial_support import (
    build_task,
    docker_context,
    docker_target_ready,
    run_trial_sync,
)
from tests.python_guardrails_support import (
    SPECS_FILE,
    assemble_verify_root,
    experiment_gate_use,
    implemented_rules,
)

INNER = "tests.test_component_python_guardrails_trial:TamperingInnerAgent"
SHADOW_INNER = "tests.test_component_python_guardrails_trial:ObservingInnerAgent"
REF = "python-guardrails@8"
TEST_BAD = (
    "printf 'import unittest\\n\\nimport bad\\n\\n\\nclass BadTest(unittest.TestCase):\\n"
    "    def test_imports(self) -> None:\\n        self.assertTrue(bad)\\n' > test_bad.py"
)

docker_trial = pytest.mark.skipif(
    not (docker_target_ready()),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
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
                f"printf 'import os\\n' > bad.py && {TEST_BAD} && "
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
    gate = {**_gate_use(), "max_rounds": 1, "timeout_sec": 120}
    compiled = compile_uses(use("python-guardrails@8"), use("guardrail-gate@4", **gate))
    task = tmp_path / "python-guardrails"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})
    assert compiled.agent_wrapper is not None
    agent = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": INNER}},
    )

    trial = run_trial_sync(task, tmp_path / "trials", agent, task.name, trial_provisions(compiled))

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


class ObservingInnerAgent(BaseAgent):
    """Lists what it can see in the workspace, and writes a refused file at step 1."""

    sessions: ClassVar[list[str]] = []

    @staticmethod
    def name() -> str:
        return "observing-inner"

    def version(self) -> str | None:
        return "1"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        cls = type(self)
        cls.sessions.append(instruction)
        write = "printf 'import os\\n' > bad.py && " if len(cls.sessions) == 1 else ""
        trajectory = {
            "schema_version": "ATIF-v1.6",
            "session_id": f"s{len(cls.sessions)}",
            "agent": {"name": "codex", "version": "1"},
            "steps": [{"step_id": 1, "source": "agent", "message": "done"}],
        }
        await environment.exec(
            f"{write}ls -A > {self.environment_logs_dir}/seen.txt; "
            f"echo {shlex.quote(json.dumps(trajectory))} > "
            f"{self.environment_logs_dir}/trajectory.json"
        )
        context.n_input_tokens, context.n_output_tokens, context.cost_usd = 10, 1, 0.0


@docker_trial
def test_the_shadow_baseline_records_the_gate_unseen_in_a_real_trial(tmp_path: Path) -> None:
    """r1's condition: the pack without workspace copies and the gate in shadow mode."""
    ObservingInnerAgent.sessions.clear()
    gate = {**_gate_use("shadow"), "timeout_sec": 120}
    compiled = compile_uses(
        use("python-guardrails@8", workspace_configs=False), use("guardrail-gate@4", **gate)
    )
    task = tmp_path / "python-guardrails-shadow"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})
    assert compiled.agent_wrapper is not None
    agent = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": SHADOW_INNER}},
    )

    trial = run_trial_sync(task, tmp_path / "trials", agent, task.name, trial_provisions(compiled))

    steps = [trial / "steps" / step / "agent" for step in ("checkpoint_1", "checkpoint_2")]
    for index, agent_dir in enumerate(steps):
        report = compiled.probe_step(agent_dir, index)
        assert report.passed, report.to_json()
        record = json.loads((agent_dir / "guardrail-gate" / GATE_RECORD).read_text())
        assert (record["mode"], record["termination_reason"]) == ("shadow", "shadow_recorded")
        assert [stage["stage"] for stage in record["stages"]] == ["work"]
        assert record["gate_clean_at_step_end"] is None
        (run,) = record["gate_runs"]
        assert (run["passed"], run["exit_code"], run["committed"]) == (False, 1, None)
        assert run["git"]["repository"] == "shadow"
        assert "A  bad.py" in run["git"]["status_porcelain"]
        gate_report = json.loads(
            next((agent_dir / "guardrail-gate").rglob("gate-*.json")).read_text()
        )
        assert gate_report["counts"]["block"] == {"F401": 1, "TEST.missing": 1}
        seen = set(next(agent_dir.rglob("seen.txt")).read_text().split())
        assert not seen & {".git", "ruff.toml", "mypy.ini"}, seen
        assert condition_step_flags(compiled, agent_dir, index) == ()
    assert len(ObservingInnerAgent.sessions) == 2, "no fix session ever starts"
    assert all("gate" not in prompt for prompt in ObservingInnerAgent.sessions)


# ---- rule-coverage verification (opt-in) -------------------------------------------

coverage_verification = pytest.mark.skipif(
    os.environ.get("RAIDAR_GUARDRAILS_VERIFY") != "1" or not (docker_target_ready()),
    reason="opt-in (make guardrails-verify): OrbStack, the pack image and PyPI egress",
)

# The lockfile-parity canary's project; its uv.lock is made in the image by the gate's uv.
DEMO_PYPROJECT = (
    '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\ndependencies = []\n'
)
VERIFY_SCRIPT = """
set -euo pipefail
{install}
chown -R agent:agent /verify
su agent -s /bin/bash -c '
set -euo pipefail
demo="$(mktemp -d)" && cp /verify/demo-pyproject.toml "$demo/pyproject.toml" && cd "$demo"
uv lock --offline --quiet && cp uv.lock /verify/demo-uv.lock
cd /verify
python3 make_fixtures.py canaries/fixtures demo-uv.lock specs.json
python3 run_canaries.py specs.json canaries/fixtures > canaries.out 2>&1 \\
  || echo "canaries_rc=$?" >> canaries.out
# Negative control: a copy of the pack with checkers disabled must fail their canaries.
cp -R /opt/raidar-tools/python-guardrails /tmp/mutant
sed -i "s/^off = \\[/off = [\\n    \\"F401\\",/" /tmp/mutant/config/policy.toml
sed -i "s/\\"C901\\", //" /tmp/mutant/config/ruff.toml
sed -i "s/^CLASS_MAX_LINES = 700/CLASS_MAX_LINES = 100000/" \\
  /tmp/mutant/lib/guardrails/source_rules.py
{mutate}
GUARDRAILS_HOME=/tmp/mutant python3 run_canaries.py specs.json canaries/fixtures \\
  --rules {mutated} > mutant.out 2>&1 || true
'
"""
# The payload is staged and installed as at environment start.
INSTALL = (
    "cp -R /verify/pack /tmp/raidar-stage"
    "\nRAIDAR_PAYLOAD_DIR=/tmp/raidar-stage RAIDAR_AGENT_USER=agent bash /verify/install.sh"
    " >/dev/null"
)
TEST_CLASS_CANARY = "RUFF.LINT.per-file-ignores.test-public-methods"
MUTATED = (
    "PYQ200",
    "RUFF.SELECT.C901",
    "RUFF.SELECT.F",
    "TEST.coverage-minimum",
    "SCOPE.shebang-script",
    "SCOPE.py-symlink",
    TEST_CLASS_CANARY,
)
# The mutant: the coverage minimum set to 0, the scope without shebang scripts and without
# ``.py`` symlink targets, and the test files' PLR0904 ignore removed (their 21-method test
# classes are refused again).
MUTATE = (
    'sed -i "s/^COVERAGE_MINIMUM_PERCENT = 85.0/COVERAGE_MINIMUM_PERCENT = 0.0/" '
    "/tmp/mutant/lib/guardrails/tests_check.py\n"
    'sed -i "s/^SHEBANG_PREFIX = b\\"#!\\"/SHEBANG_PREFIX = b\\"#!never\\"/" '
    "/tmp/mutant/lib/guardrails/scope.py\n"
    'sed -i "s/if target is not None and target.resolve() not in seen:/if False:/" '
    "/tmp/mutant/lib/guardrails/scope.py\n"
    'sed -i "s/\\"PLR2004\\", \\"PLR0904\\"]/\\"PLR2004\\"]/" /tmp/mutant/config/ruff.toml'
)
MUTANT_FAILURE = {TEST_CLASS_CANARY: "passing fixture exited 1"}
"""How a mutant fails a canary whose clean fixture (not its violating one) the mutant breaks."""


def _gate_use(mode: str = "commit") -> dict[str, Any]:
    """The experiment's gate ``with`` in ``mode`` without its autofix keys."""
    gate = experiment_gate_use(mode)
    return {key: value for key, value in gate.items() if not key.startswith("autofix")}


@coverage_verification
def test_every_implemented_rule_is_proven_by_its_canaries_in_the_pack_image(
    tmp_path: Path,
) -> None:
    root = assemble_verify_root(tmp_path / "verify")
    (root / "install.sh").write_text(install_script())
    (root / "run.sh").write_text(
        VERIFY_SCRIPT.format(
            install=INSTALL,
            mutate=MUTATE,
            mutated=",".join(MUTATED),
        )
    )
    (root / "demo-pyproject.toml").write_text(DEMO_PYPROJECT)
    name = f"raidar-guardrails-verify-{uuid.uuid4().hex[:8]}"

    done = subprocess.run(
        ["docker", "--context", docker_context(), "run", "--rm", "--name", name]
        + ["-v", f"{root}:/verify", "--user", "root", image_tag(load_pack_spec())]
        + ["bash", "/verify/run.sh"],
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )

    output = (root / "canaries.out").read_text() if (root / "canaries.out").exists() else ""
    print(output)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (root / "specs.json").read_text() == SPECS_FILE.read_text(), (
        "the image's fixture generator wrote different canary specs"
    )
    failures = [line for line in output.splitlines() if line.startswith("canary FAIL")]
    assert failures == [], "\n".join(failures)
    passed = [line for line in output.splitlines() if line.startswith("canary PASS ")]
    expected = implemented_rules(json.loads(SPECS_FILE.read_text()))
    assert {line.split()[2] for line in passed} == expected
    assert f"canaries: {len(expected)} rule(s), 0 failed" in output
    mutant = (root / "mutant.out").read_text()
    for rule in MUTATED:
        why = MUTANT_FAILURE.get(rule, "violating canary passed")
        assert f"canary FAIL {rule}: {why}" in mutant, mutant[-2000:]
    assert f"canaries: {len(MUTATED)} rule(s), {len(MUTATED)} failed" in mutant


# ---- --autofix canaries with the pinned Ruff (opt-in) ----------------------------------------

AUTOFIX_CANARIES = {
    "calc.py": "def add(a,b) :\n    return  a+b\n",
    "main.py": "import sys\nimport os\n\nprint(os.sep, sys.platform)\n",
    "tests/test_unsafe.py": "def test_f() -> None:\n    unused = 1\n    assert True\n",
    "notes.txt": "x  =  1\n",
}
AUTOFIX_VERIFY = """
set -euo pipefail
{install}
chown -R agent:agent /verify
su agent -s /bin/bash -c '
set -euo pipefail
for home in /opt/raidar-tools/python-guardrails /tmp/mutant; do
  name="$(basename "$home")"
  if [ "$name" = mutant ]; then
    cp -R /opt/raidar-tools/python-guardrails /tmp/mutant
    sed -i "s/\"--no-unsafe-fixes\"/\"--unsafe-fixes\"/" /tmp/mutant/lib/guardrails/autofix.py
  fi
  rm -rf "/tmp/ws-$name" && cp -R /verify/workspace "/tmp/ws-$name"
  GUARDRAILS_HOME="$home" guardrail-check --autofix --root "/tmp/ws-$name" \
    --json-out "/verify/autofix-$name.json" > "/verify/autofix-$name.out" 2>&1 \
    || echo "rc=$?" >> "/verify/autofix-$name.out"
  cp -R "/tmp/ws-$name" "/verify/after-$name"
done
'
"""


@coverage_verification
def test_autofix_is_proven_by_its_canaries_in_the_pack_image(tmp_path: Path) -> None:
    """Pinned Ruff: FORMAT and I001 fixed, F841 kept, notes.txt untouched; the mutant fails."""
    root = tmp_path / "verify"
    for relative in PAYLOAD_FILES:
        target = root / "pack" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD_DIR / relative, target)  # keeps the executable bits
    for relative, text in AUTOFIX_CANARIES.items():
        target = root / "workspace" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (root / "install.sh").write_text(install_script())
    (root / "run.sh").write_text(AUTOFIX_VERIFY.format(install=INSTALL))
    name = f"raidar-guardrails-autofix-{uuid.uuid4().hex[:8]}"

    done = subprocess.run(
        ["docker", "--context", docker_context(), "run", "--rm", "--name", name]
        + ["-v", f"{root}:/verify", "--user", "root", image_tag(load_pack_spec())]
        + ["bash", "/verify/run.sh"],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((root / "autofix-python-guardrails.json").read_text())
    print(json.dumps(report, indent=2))
    assert (report["pack"], report["verdict"]) == (REF, "fixed")
    assert {"FORMAT", "I001"} <= set(report["codes_fixed"])
    assert "F841" not in report["codes_fixed"]
    assert report["codes_after"] == {"F841": 1}
    assert report["files_changed"] == ["calc.py", "main.py"]
    after = root / "after-python-guardrails"
    assert (after / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert (after / "tests" / "test_unsafe.py").read_text() == AUTOFIX_CANARIES[
        "tests/test_unsafe.py"
    ]
    assert (after / "notes.txt").read_text() == AUTOFIX_CANARIES["notes.txt"]
    # Negative control: a pack that allowed unsafe fixes fails the F841 canary.
    mutant = json.loads((root / "autofix-mutant.json").read_text())
    assert "F841" in mutant["codes_fixed"], mutant
    assert "unused = 1" not in (root / "after-mutant" / "tests" / "test_unsafe.py").read_text()
