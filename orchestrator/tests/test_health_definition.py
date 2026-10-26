"""Health family E over the define stage's records: what its checks accept and refuse."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from raidar.engine.health.definition import definition_family
from raidar.subject.workflow.define_tool import tool

BLOCK = """title: Greeting
objective: The greet command prints a greeting.
feature F1: Greeting
outcome: greet prints Hello.
ac F1.AC1: greet prints Hello
source: U1
aspect: greet is run -> Hello is printed
example: greet -> `Hello`
"""
STEP = "checkpoint_1"


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _sealed_step(tmp_path: Path, *, verify: str | None = "gate_passed") -> Path:
    """A step's agent logs with a define stage that sealed a real draft with the real tool."""
    agent = tmp_path / "agent"
    state = tmp_path / "state"
    context = tool.Context("The greet command prints Hello.\n", (), "", (), str(tmp_path), 2)
    tool.start(state, context)
    output = tmp_path / "feature-definition.md"
    tool.run(state, ["put"], lambda: BLOCK)
    sealed = tool.run(state, ["done", "ready"], lambda: "", output)
    assert sealed.code == 0, sealed.output
    define = agent / "workflow" / "define"
    for name, source in (
        ("inputs.json", state / tool.CONTEXT_FILE),
        ("state.json", state / tool.STATE_FILE),
        ("define-calls.jsonl", state / tool.CALLS_FILE),
        ("define-events.jsonl", state / tool.EVENTS_FILE),
        ("feature-definition.md", output),
    ):
        _write(define / name, source.read_bytes())
    record: dict[str, Any] = {
        "ending": "definition_ready",
        "inputs": "workflow/define/inputs.json",
        "state": "workflow/define/state.json",
        "calls": "workflow/define/define-calls.jsonl",
        "events": "workflow/define/define-events.jsonl",
        "definition": "workflow/define/feature-definition.md",
        "definition_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "checks": [{"after": "agent", "sealed": True, "status": "passed", "blocking": 0}],
        "repairs": [],
        "questions": [],
        "unit_rules": 2,
        "max_refinement_rounds": 2,
        "source_commit": "ac5db0f28364911ef2298e428748120be8b0d56c",
    }
    workflow: dict[str, Any] = {"stages": ["define", "verify"], "define": record}
    if verify:
        workflow["verify"] = {"ending": verify}
    _write(agent / "workflow" / "workflow.json", json.dumps(workflow))
    return agent


def _status(agent: Path) -> dict[str, str]:
    return {c.id: c.status for c in definition_family([(STEP, agent)])}


def _rewrite(agent: Path, **record: Any) -> None:
    path = agent / "workflow" / "workflow.json"
    workflow = json.loads(path.read_text())
    workflow["define"].update(record)
    path.write_text(json.dumps(workflow))


def test_a_sealed_definition_the_gate_passed_holds_on_every_check(tmp_path: Path) -> None:
    assert _status(_sealed_step(tmp_path)) == {"E1": "pass", "E2": "pass", "E3": "pass"}


def test_a_trial_without_a_define_stage_has_no_e_checks(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    _write(agent / "workflow" / "workflow.json", json.dumps({"stages": ["verify"]}))
    assert definition_family([(STEP, agent)]) == ()


def test_a_state_that_breaks_the_reply_schema_is_a_violation_of_e1(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    path = agent / "workflow" / "define" / "state.json"
    state = json.loads(path.read_text())
    criterion = next(i for i in state["items"] if i["kind"] == "ac")
    criterion["data"]["aspects"] = 7
    path.write_text(json.dumps(state))
    found = {c.id: c for c in definition_family([(STEP, agent)])}["E1"]
    assert found.status == "violation"


def test_a_hand_edited_state_is_a_violation_of_e2(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    state = agent / "workflow" / "define" / "state.json"
    state.write_text(state.read_text().replace("Greeting", "Salutation"))
    assert _status(agent)["E2"] == "violation"


def test_inputs_that_are_not_the_context_the_last_call_ran_under_violate_e2(
    tmp_path: Path,
) -> None:
    agent = _sealed_step(tmp_path)
    inputs = agent / "workflow" / "define" / "inputs.json"
    inputs.write_text(inputs.read_text() + " ")
    assert _status(agent)["E2"] == "violation"


def test_a_call_log_with_a_line_removed_is_a_violation_of_e2(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    calls = agent / "workflow" / "define" / "define-calls.jsonl"
    calls.write_text(calls.read_text().splitlines()[-1] + "\n")
    assert _status(agent)["E2"] == "violation"


def test_a_definition_file_that_is_not_the_draft_render_is_a_violation_of_e1(
    tmp_path: Path,
) -> None:
    agent = _sealed_step(tmp_path)
    written = agent / "workflow" / "define" / "feature-definition.md"
    edited = written.read_text() + "\nAn added line.\n"
    written.write_text(edited)
    _rewrite(agent, definition_sha256=hashlib.sha256(edited.encode()).hexdigest())
    assert _status(agent)["E1"] == "violation"


def test_a_definition_whose_digest_is_not_the_recorded_one_is_a_violation_of_e1(
    tmp_path: Path,
) -> None:
    agent = _sealed_step(tmp_path)
    _rewrite(agent, definition_sha256="0" * 64)
    assert _status(agent)["E1"] == "violation"


def test_a_gate_pass_without_a_ready_definition_is_a_violation_of_e1(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    _rewrite(agent, ending="definition_unusable", definition=None, definition_sha256=None)
    assert _status(agent)["E1"] == "violation"


def test_missing_define_evidence_is_unmeasured_never_pass(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    (agent / "workflow" / "define" / "define-events.jsonl").unlink()
    (agent / "workflow" / "define" / "inputs.json").unlink()
    assert _status(agent) == {"E1": "unmeasured", "E2": "unmeasured", "E3": "pass"}


def test_an_unusable_definition_is_recorded_and_not_a_violation_when_the_gate_did_not_pass(
    tmp_path: Path,
) -> None:
    agent = _sealed_step(tmp_path, verify="gate_refused")
    checks = [{"after": "repair", "sealed": False, "status": "failed", "blocking": 2}]
    _rewrite(
        agent, ending="definition_unusable", definition=None, definition_sha256=None, checks=checks
    )
    assert _status(agent)["E3"] == "pass"


def test_a_draft_sealed_ask_at_the_refinement_limit_is_a_recorded_unusable_ending(
    tmp_path: Path,
) -> None:
    agent = _sealed_step(tmp_path, verify="gate_refused")
    state = agent / "workflow" / "define" / "state.json"
    state.write_text(state.read_text().replace('"sealed": "ready"', '"sealed": "ask"'))
    checks = [{"after": "agent", "sealed": True, "status": "passed", "blocking": 0}]
    _rewrite(
        agent, ending="definition_unusable", definition=None, definition_sha256=None, checks=checks
    )
    assert _status(agent)["E3"] == "pass"


def test_a_ready_draft_called_unusable_is_a_violation_of_e3(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path, verify="gate_refused")
    _rewrite(agent, ending="definition_unusable", definition=None, definition_sha256=None)
    assert _status(agent)["E3"] == "violation"


def test_a_step_naming_define_without_a_record_is_unmeasured(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    _write(agent / "workflow" / "workflow.json", json.dumps({"stages": ["define", "verify"]}))
    assert _status(agent) == {"E1": "unmeasured", "E2": "unmeasured", "E3": "unmeasured"}


def test_a_malformed_define_record_is_unmeasured_on_every_check(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    _rewrite(agent, unit_rules=1, source_commit="ac5db0f", events=None)
    assert _status(agent) == {"E1": "unmeasured", "E2": "unmeasured", "E3": "unmeasured"}


def test_an_unusable_definition_the_gate_passed_is_a_violation_of_e3(tmp_path: Path) -> None:
    agent = _sealed_step(tmp_path)
    checks = [{"after": "repair", "sealed": False, "status": "failed", "blocking": 2}]
    _rewrite(
        agent, ending="definition_unusable", definition=None, definition_sha256=None, checks=checks
    )
    assert _status(agent)["E3"] == "violation"
