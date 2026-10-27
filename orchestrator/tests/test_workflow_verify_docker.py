"""Fake-agent attacks on the verify stage in a real container (a ready Docker target only).

No model runs. A scripted attacker, running as the agent's user exactly as a model's tool calls
would, plants a ``conftest.py`` and a repository configuration (``core.fsmonitor`` and a clean and
process filter) that try to write into the gate's trusted files (``/opt/raidar-verify``), the hook
log and state, the hook broker's socket, the gate scratch, the define state (with the define stage
really enabled) and the native session logs, with the write-and-restore and the plain-rewrite
forms. Each attempt must fail (the trusted files, the hook log, the socket) or be detected as a
named integrity violation (what the agent's user can legitimately write: the session logs and the
define state), a forged hook record must be refused, and nothing of the agent's may run as root.
A throwaway negative control runs the archived pre-S6 root seal on the same plant and shows the
plant executes as root there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.config import AgentConfig

from raidar.engine.health.gate import gate_family
from raidar.subject.workflow.definition import load_workflow
from raidar.subject.workflow.inner import INNER_KWARG, InnerAgentSpec
from raidar.subject.workflow.runner import WorkflowRunner
from tests.docker_trial_support import build_task, docker_target_ready, run_trial_sync
from tests.release_support import REAL_LAB
from tests.workflow_attack_support import (
    ATTACK_DEFINE,
    ATTACK_MODE,
    DEFINE_REPLY,
    PLANTED,
    AttackAgent,
    NegativeControlAgent,
    pre_s6_gate,
)
from tests.workflow_support import ScriptedAgent

pytestmark = pytest.mark.skipif(
    not docker_target_ready(),
    reason="needs a ready Docker target (data check passing) and the built python pack image",
)

STEP = "checkpoint_1"
REPOSITORY = Path(__file__).resolve().parents[2]
AGENT_UID = 1000
TESTS_CHECK = {"name": "tests", "category": "tests", "tool": "pytest==9.1.1", "blocking": True}
SCENARIOS = [
    ("restore", False),
    ("rewrite", False),
    ("restore", True),
    ("rewrite", True),
]


def definition(defining: bool) -> tuple[str, dict[str, Any]]:
    """The workflow's reference and resolved definition: one-shot-verify, or define-build-verify
    without its judge (the define stage really enabled), with the tests check and two fixes."""
    name = "define-build-verify" if defining else "one-shot-verify"
    resolved: Any = load_workflow(REAL_LAB / "workflows", name).to_json()
    stages = resolved["stages"]
    stages["verify"]["checks"] = [TESTS_CHECK]
    stages["verify"]["max_fix_rounds"] = 2
    if defining:
        for key in ("judge", "rework"):
            stages["verify"].pop(key, None)
        stages["verify"]["max_passes"] = 1
    return f"{name}@attack", resolved


def attack_config(defining: bool) -> AgentConfig:
    native = InnerAgentSpec(name="attack", import_path=AttackAgent.import_path())
    reference, resolved = definition(defining)
    return AgentConfig(
        import_path=WorkflowRunner.import_path(),
        model_name="openai/gpt-6-luna",
        kwargs={
            INNER_KWARG: native.model_dump(mode="json"),
            "workflow": reference,
            "definition": resolved,
            "skills_placement": ".agents/skills",
            "phase_timeout_s": 900.0,
        },
        override_timeout_sec=1200.0,
    )


def run_attack(base: Path, mode: str, defining: bool) -> Path:
    """One real trial with the attacker; the step's agent logs directory."""
    task = base / "task"
    build_task(task, {STEP: "Implement calc.add"}, agent_timeout_sec=1200)

    def reply(workspace: Path, prompt: str) -> str | None:
        return DEFINE_REPLY if defining else None

    ScriptedAgent.turns = [reply] * 6
    ScriptedAgent.prompts = []
    ScriptedAgent.workspace = Path("/nonexistent")
    os.environ[ATTACK_MODE] = mode
    os.environ[ATTACK_DEFINE] = "1" if defining else "0"
    try:
        trial = run_trial_sync(task, base / "trials", attack_config(defining), f"attack-{mode}")
    finally:
        os.environ.pop(ATTACK_MODE, None)
        os.environ.pop(ATTACK_DEFINE, None)
    return trial / "steps" / STEP / "agent"


@pytest.fixture(scope="module", params=SCENARIOS, ids=["-".join(map(str, s)) for s in SCENARIOS])
def attacked(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, str, bool]:
    mode, defining = request.param
    base = tmp_path_factory.mktemp(f"attack-{mode}-{int(defining)}")
    return run_attack(base, mode, defining), mode, defining


def workflow(agent: Path) -> dict[str, Any]:
    record: dict[str, Any] = json.loads((agent / "workflow" / "workflow.json").read_text())
    return record


def attacks(agent: Path) -> list[dict[str, Any]]:
    lines = (agent / "raidar-attacks.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def outcomes(agent: Path, name: str) -> set[str]:
    found = {
        result["outcome"]
        for attack in attacks(agent)
        if attack["label"] == "conftest"
        for result in attack["results"]
        if result["attack"] == name
    }
    assert found, f"no conftest run attempted {name}"
    return found


def test_the_gate_set_up_ran_the_conftest_in_the_hook_and_the_seals(
    attacked: tuple[Path, str, bool],
) -> None:
    agent, _, defining = attacked
    stage = workflow(agent)["verify"]
    assert stage["setup_check"]["status"] == "passed"
    assert stage["gates"]
    if not defining:  # with the define state attacked the fix turn may rightly stay refused
        assert [g["verdict"] for g in stage["gates"]] == ["refused", "passed"]
        assert stage["ending"] == "gate_passed"


def test_no_gate_step_and_no_agent_code_ran_as_root_and_the_broker_logged_the_hooks(
    attacked: tuple[Path, str, bool],
) -> None:
    agent, mode, defining = attacked
    stage = workflow(agent)["verify"]
    seals = [gate["process"] for gate in stage["gates"]]
    hook_lines = (agent / stage["hook_log"]).read_text().splitlines()
    hooks = [json.loads(line) for line in hook_lines]
    assert hooks, "the broker logged no hook run"
    facts = [stage["setup_check"]["process"], *seals, *(hook["process"] for hook in hooks)]
    assert all(f["uid"] == f["euid"] == AGENT_UID for f in facts), facts
    assert all(hook["broker"]["uid"] == 0 for hook in hooks)  # only the broker is root
    assert all(hook["peer_uid"] == AGENT_UID for hook in hooks)  # whoever asked was the agent
    trusted_ancestors = {1, *(hook["broker"]["pid"] for hook in hooks)}
    for attack in attacks(agent):
        assert attack["uid"] == attack["euid"] == AGENT_UID, attack
        unexpected_root = [
            parent
            for parent in attack["ancestry"]
            if parent["uid"] == 0 and parent["pid"] not in trusted_ancestors
        ]
        assert unexpected_root == [], attack["ancestry"]
    broker_pids = trusted_ancestors - {1}
    in_hook = [a for a in attacks(agent) if any(p["pid"] in broker_pids for p in a["ancestry"])]
    in_seal = [a for a in attacks(agent) if a not in in_hook and a["label"] == "conftest"]
    assert in_hook, "no attack ran in a hook decision"  # a real attempt, not an incidental count
    if not (defining and mode == "rewrite"):  # a state the hook broke makes the seal stop early
        assert in_seal, "no attack ran in a seal"


def test_the_root_owned_tools_hook_state_and_broker_cannot_be_written_or_forged(
    attacked: tuple[Path, str, bool],
) -> None:
    agent, _, _ = attacked
    trusted = [
        result
        for attack in attacks(agent)
        if attack["label"] == "conftest"
        for result in attack["results"]
        if result["attack"].startswith(("write:/opt/", "rename:/opt/", "unlink:/opt/"))
    ]
    assert {result["attack"] for result in trusted} == {
        "write:/opt/raidar-verify/verify_gate.py",
        "write:/opt/raidar-verify/gate.json",
        "write:/opt/raidar-verify/rules.md",
        "write:/opt/raidar-verify/hooks/pre-commit",
        "write:/opt/raidar-verify/tools/bin/python",
        "rename:/opt/raidar-verify/hooks",
        "unlink:/opt/raidar-verify/hooks/pre-commit",
    }
    assert {result["outcome"] for result in trusted} == {"denied"}
    for name in (
        "hook-log",
        "hook-log-unlink",
        "hook-state-rename",
        "broker-socket-unlink",
        "parent-record-pipe",
    ):
        assert outcomes(agent, name) == {"denied"}, name
    assert outcomes(agent, "broker-forged-request") == {"rejected"}
    tools = workflow(agent)["verify"]["tools"]
    assert tools["sha256_before"] == tools["sha256_after"]
    assert {c.id: c.status for c in gate_family([(STEP, agent)])}["G3"] == "pass"


def test_what_the_agents_user_can_write_is_detected_as_a_named_violation(
    attacked: tuple[Path, str, bool],
) -> None:
    agent, mode, defining = attacked
    record = workflow(agent)
    verify_names = {item["name"] for item in record["verify"]["integrity"]}
    session_names = verify_names | {item["name"] for item in record["integrity"]}
    assert not {n for n in verify_names if n.startswith("hook_log")}, verify_names  # denied
    if mode == "restore":
        assert "session_log_touched" in session_names, session_names
        assert ("define_state_touched" in verify_names) is defining, verify_names
    else:
        assert "session_log_rewritten" in session_names, session_names
        assert ("define_state_rewritten" in verify_names) is defining, verify_names


def test_the_planted_repository_configuration_is_never_run_by_the_gate(
    attacked: tuple[Path, str, bool],
) -> None:
    labels = {attack["label"] for attack in attacks(attacked[0])}
    assert labels.isdisjoint(PLANTED), labels  # nothing after the plant executed it


def test_the_archived_root_seal_ran_the_planted_commands_as_root(tmp_path: Path) -> None:
    """The negative control (throwaway container only): the same plant, the pre-S6 root seal."""
    try:
        NegativeControlAgent.gate_source = pre_s6_gate(REPOSITORY)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    task = tmp_path / "task"
    build_task(task, {STEP: "negative control"}, agent_timeout_sec=600)
    config = AgentConfig(import_path=NegativeControlAgent.import_path(), override_timeout_sec=600.0)
    trial = run_trial_sync(task, tmp_path / "trials", config, "negative-control")
    text = (trial / "steps" / STEP / "agent" / "raidar-negative-control.txt").read_text()
    sentinels: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("SENTINEL "):
            _, path, uids = line.split(" ", 2)
            sentinels[path] = uids
    assert sentinels.get("/tmp/nc-sentinel-fsmonitor", "").startswith("0,"), text
    assert sentinels.get("/tmp/nc-sentinel-filter-clean", "").startswith("0,"), text
