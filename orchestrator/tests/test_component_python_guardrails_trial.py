"""python-guardrails@1 in the pack image on OrbStack (no LLM).

- A real two-step Harbor trial: python-guardrails@1 with guardrail-gate@1 running
  the experiment's own hook command, around a scripted inner agent that writes
  a refused file, edits the workspace ``ruff.toml`` copy and runs
  ``git commit --no-verify``, then fixes the file when the gate refuses. It
  proves the install, the per-step probe, the commit refusal with the pinned
  tools, the per-run JSON reports and the integrity flags end to end.
- The rule tests (``make guardrails-verify``; opt-in with
  ``RAIDAR_GUARDRAILS_VERIFY=1`` because it runs two canary gate runs per rule),
  for each pack under verification (python-guardrails@1, whose payload @2
  installs, and @3 to @6): every implemented rule's violating canary fires and its
  clean canary passes (``run_canaries.py``) against the pack installed in the
  image by its own install script, the parity canary through the experiment's
  guardrail-gate commit script; a mutant pack with checkers disabled (three,
  and @3's and @4's coverage minimum) must fail exactly those rules' violating
  canaries (@4's mutant also has its scope reduced to @3's, so both ``SCOPE.*``
  canaries must fail; @6's mutant also has @5's test-file ignores, so the
  test-class canary's clean fixture is refused). No file of the
  setup-repository-guardrails skill is involved.
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

from raidar.components.builtins import (
    guardrails_v3,
    guardrails_v4,
    guardrails_v5,
    guardrails_v6,
    guardrails_v7,
)
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
from tests.python_guardrails_support import (
    CANARY_PACK,
    PACKS,
    PackUnderTest,
    assemble_verify_root,
    experiment_gate_use,
    implemented_rules,
)

INNER = "tests.test_component_python_guardrails_trial:TamperingInnerAgent"
SHADOW_INNER = "tests.test_component_python_guardrails_trial:ObservingInnerAgent"
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
    # Released @1/@2 behaviour is proven as released: no tests check, install at step 1.
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
    gate = {**experiment_gate_use("shadow"), "timeout_sec": 120}
    compiled = compile_uses(
        use("python-guardrails@2", workspace_configs=False), use("guardrail-gate@2", **gate)
    )
    task = tmp_path / "python-guardrails-shadow"
    build_task(task, compiled, {"checkpoint_1": "step one", "checkpoint_2": "step two"})
    assert compiled.agent_wrapper is not None
    agent = AgentConfig(
        import_path=compiled.agent_wrapper.import_path,
        kwargs={**dict(compiled.agent_wrapper.kwargs), "inner_agent": {"name": SHADOW_INNER}},
    )

    trial = run_trial_sync(
        task, tmp_path / "trials", agent, task.name, condition_provisions(compiled)
    )

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
        assert gate_report["counts"]["block"] == {"F401": 1}
        seen = set(next(agent_dir.rglob("seen.txt")).read_text().split())
        assert not seen & {".git", "ruff.toml", "mypy.ini"}, seen
        assert condition_step_flags(compiled, agent_dir, index) == ()
    assert len(ObservingInnerAgent.sessions) == 2, "no fix session ever starts"
    assert all("gate" not in prompt for prompt in ObservingInnerAgent.sessions)


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
{install}
chown -R agent:agent /verify
su agent -s /bin/bash -c '
set -euo pipefail
demo="$(mktemp -d)" && cp /verify/demo-pyproject.toml "$demo/pyproject.toml" && cd "$demo"
uv lock --offline --quiet && cp uv.lock /verify/demo-uv.lock
cd /verify
python3 make_fixtures.py canaries/fixtures demo-uv.lock specs.json {pack_args}
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
INSTALL = {
    1: (
        "mkdir -p /tmp/raidar-components/{slug} && "
        "cp -R /verify/pack /tmp/raidar-components/{slug}/"
        "\nbash /verify/install.sh >/dev/null"
    ),
    3: (
        "cp -R /verify/pack /tmp/raidar-stage"
        "\nRAIDAR_PAYLOAD_DIR=/tmp/raidar-stage RAIDAR_AGENT_USER=agent bash /verify/install.sh"
        " >/dev/null"
    ),
}
INSTALL[4] = INSTALL[3]
INSTALL[5] = INSTALL[3]
MUTATED = {
    1: ("PYQ200", "RUFF.SELECT.C901", "RUFF.SELECT.F"),
    3: ("PYQ200", "RUFF.SELECT.C901", "RUFF.SELECT.F", "TEST.coverage-minimum"),
    4: (
        "PYQ200",
        "RUFF.SELECT.C901",
        "RUFF.SELECT.F",
        "TEST.coverage-minimum",
        "SCOPE.shebang-script",
        "SCOPE.py-symlink",
    ),
}
MUTATE = {
    1: "",
    3: (
        'sed -i "s/^COVERAGE_MINIMUM_PERCENT = 85.0/COVERAGE_MINIMUM_PERCENT = 0.0/" '
        "/tmp/mutant/lib/guardrails/tests_check.py"
    ),
}
MUTATE[4] = MUTATE[3] + (
    "\n"
    'sed -i "s/^SHEBANG_PREFIX = b\\"#!\\"/SHEBANG_PREFIX = b\\"#!never\\"/" '
    "/tmp/mutant/lib/guardrails/scope.py\n"
    'sed -i "s/if target is not None and target.resolve() not in seen:/if False:/" '
    "/tmp/mutant/lib/guardrails/scope.py"
)

MUTATED[5], MUTATE[5] = MUTATED[4], MUTATE[4]
TEST_CLASS_CANARY = "RUFF.LINT.per-file-ignores.test-public-methods"
INSTALL[6] = INSTALL[3]
MUTATED[6] = (*MUTATED[4], TEST_CLASS_CANARY)
# @6's mutant has @5's test-file ignores: its 21-method test classes are refused again.
MUTATE[6] = MUTATE[4] + (
    '\nsed -i "s/\\"PLR2004\\", \\"PLR0904\\"]/\\"PLR2004\\"]/" /tmp/mutant/config/ruff.toml'
)
MUTANT_FAILURE = {TEST_CLASS_CANARY: "passing fixture exited 1"}
"""How a mutant fails a canary whose clean fixture (not its violating one) the mutant breaks."""
# @7's rules are @6's byte for byte; its install also exposes pytest and coverage to the
# image's Python, so its canaries run with that .pth file in place.
INSTALL[7], MUTATED[7], MUTATE[7] = INSTALL[6], MUTATED[6], MUTATE[6]
INSTALLERS = {
    3: guardrails_v3,
    4: guardrails_v4,
    5: guardrails_v5,
    6: guardrails_v6,
    7: guardrails_v7,
}


def _install(pack: PackUnderTest) -> str:
    if pack.version == 1:
        return install_script(VERIFY_SLUG)
    return INSTALLERS[pack.version].install_script()


@coverage_verification
@pytest.mark.parametrize("version", sorted(PACKS))
def test_every_implemented_rule_is_proven_by_its_canaries_in_the_pack_image(
    tmp_path: Path, version: int
) -> None:
    pack = PACKS[version]
    mutated = MUTATED[version]
    root = assemble_verify_root(tmp_path / "verify", pack)
    (root / "install.sh").write_text(_install(pack))
    (root / "run.sh").write_text(
        VERIFY_SCRIPT.format(
            install=INSTALL[version].format(slug=VERIFY_SLUG),
            pack_args="" if version == 1 else f"--pack {CANARY_PACK.get(version, version)}",
            mutate=MUTATE[version],
            mutated=",".join(mutated),
        )
    )
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

    output = (root / "canaries.out").read_text() if (root / "canaries.out").exists() else ""
    print(output)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (root / "specs.json").read_text() == pack.specs_path.read_text(), (
        "the image's fixture generator wrote different canary specs"
    )
    failures = [line for line in output.splitlines() if line.startswith("canary FAIL")]
    assert failures == [], "\n".join(failures)
    passed = [line for line in output.splitlines() if line.startswith("canary PASS ")]
    expected = implemented_rules(json.loads(pack.specs_path.read_text()), pack)
    assert {line.split()[2] for line in passed} == expected
    assert f"canaries: {len(expected)} rule(s), 0 failed" in output
    mutant = (root / "mutant.out").read_text()
    for rule in mutated:
        why = MUTANT_FAILURE.get(rule, "violating canary passed")
        assert f"canary FAIL {rule}: {why}" in mutant, mutant[-2000:]
    assert f"canaries: {len(mutated)} rule(s), {len(mutated)} failed" in mutant
