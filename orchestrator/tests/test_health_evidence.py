"""Family W: missing lab-owned artifacts of an enabled stage invalidate the trial, row by row."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from raidar.engine.health.audit import audit_trial
from raidar.engine.trial.collect import health_validity
from tests.health_support import STEP, HealthyTrial, healthy_trial, write_trial_config

Workflow = dict[str, Any]
Mutation = Callable[[Path, Workflow], None]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _put(agent: Path, relative: str, text: str) -> str:
    path = agent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return _sha(text)


def _define_record(agent: Path) -> Workflow:
    files = {
        "inputs": "workflow/define/inputs.json",
        "state": "workflow/define/state.json",
        "calls": "workflow/define/define-calls.jsonl",
        "events": "workflow/define/define-events.jsonl",
        "definition": "workflow/define/feature-definition.md",
    }
    digests = {key: _put(agent, path, f"{key}\n") for key, path in files.items()}
    return {
        **files,
        "ending": "definition_ready",
        "closed": False,
        "checks": [],
        "definition_sha256": digests["definition"],
    }


def _judge_files(agent: Path) -> tuple[Workflow, Workflow, list[Workflow]]:
    result = {"ok": True, "answers": {"aspect_asserted": {"type": "noul", "noul": 0.95}}}
    for name, document in (("request", {"state": {}}), ("response", result), ("result", result)):
        _put(agent, f"workflow/judge/call-0.{name}.json", json.dumps(document))
    call = {
        "index": 0,
        "aspect": "F1.AC1.1",
        "probability": 0.95,
        "result": result,
        "request_file": "workflow/judge/call-0.request.json",
        "response_file": "workflow/judge/call-0.response.json",
        "result_file": "workflow/judge/call-0.result.json",
    }
    aspect = {"id": "F1.AC1.1", "calls": [0], "probability": 0.95, "status": "closed"}
    criterion = {
        "id": "F1.AC1",
        "tests": ["t"],
        "failing": [],
        "test_results": {"t": "passed"},
        "aspects": [aspect],
        "status": "closed",
    }
    passed = {
        "pass": 1,
        "gate_verdict": "passed",
        "criteria": [criterion],
        "closed": ["F1.AC1"],
        "open": [],
        "undecided": [],
    }
    return {"open_at": 0.3, "closed_at": 0.8}, passed, [call]


def _verify_record(agent: Path, *, judge: bool) -> Workflow:
    config = _put(agent, "workflow/verify/gate.json", "{}\n")
    hook = _put(agent, "workflow/verify/hook-runs.jsonl", "")
    _put(agent, "workflow/verify/gate-1.json", json.dumps({"verdict": "passed"}))
    _put(agent, "workflow/verify/base-tests.json", "{}\n")
    record: Workflow = {
        "ending": "gate_passed",
        "gate_config_sha256": config,
        "hook_log": "workflow/verify/hook-runs.jsonl",
        "hook_log_sha256": hook,
        "setup_check": {"status": "passed", "checks": []},
        "gates": [{"verdict": "passed", "tree": "t", "record": "workflow/verify/gate-1.json"}],
    }
    if judge:
        settings, passed, calls = _judge_files(agent)
        record |= {
            "judge": settings,
            "judge_calls": calls,
            "judged": [passed],
            "baseline": {"record": "workflow/verify/base-tests.json"},
            "baseline_passed": [],
            "ending": "verified",
        }
    return record


def _materialise(t: HealthyTrial, *, define: bool, verify: bool, judge: bool) -> Path:
    """The lab's config and every artifact the enabled stages leave, as a real trial would."""
    write_trial_config(t.trial_dir, define=define, verify=verify, judge=judge)
    agent = t.trial_dir / "steps" / STEP / "agent"
    path = agent / "workflow/workflow.json"
    workflow = json.loads(path.read_text())
    if define:
        workflow["stages"].append("define")
        workflow["define"] = _define_record(agent)
    if verify:
        workflow["stages"].append("verify")
        workflow["verify"] = _verify_record(agent, judge=judge)
    path.write_text(json.dumps(workflow))
    return agent


def _mutate(agent: Path, change: Mutation) -> None:
    path = agent / "workflow/workflow.json"
    workflow = json.loads(path.read_text())
    change(agent, workflow)
    if path.exists():
        path.write_text(json.dumps(workflow))


def _checks(t: HealthyTrial) -> dict[str, Any]:
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return {c.id: c for c in report.checks if c.id.startswith("W")}


def _remove(relative: str) -> Mutation:
    return lambda agent, workflow: (agent / relative).unlink()


def _set(*keys: str, value: Any) -> Mutation:
    def change(agent: Path, workflow: Workflow) -> None:
        target = workflow
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return change


def _drop(*keys: str) -> Mutation:
    def change(agent: Path, workflow: Workflow) -> None:
        target = workflow
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]

    return change


def _rollout(agent: Path, workflow: Workflow) -> None:
    turn = workflow["turns"][0]
    source = turn["session_audit"]["sources"][0]["path"]
    (agent / turn["log_dir"] / source).unlink()


def _empty_audit(agent: Path, workflow: Workflow) -> None:
    workflow["turns"][0]["session_audit"] = {}


def _bare_audit(agent: Path, workflow: Workflow) -> None:
    workflow["turns"][0]["session_audit"].update(records=0, sources=[])


ROW_CASES: list[tuple[str, Mutation]] = [
    ("W1", _remove("workflow/workflow.json")),
    ("W1", _drop("turns")),
    ("W1", _drop("define")),
    ("W2", _rollout),
    ("W2", _empty_audit),
    ("W2", _bare_audit),
    ("W3", _remove("workflow/verify/gate-1.json")),
    ("W3", _remove("workflow/verify/gate.json")),
    ("W3", _set("verify", "gates", value=[])),
    ("W4", _set("verify", "ending", value="gate_error")),
    ("W6", _drop("verify", "setup_check")),
    ("W7", _set("verify", "setup_check", value={"status": "failed", "checks": []})),
    ("W8", _drop("define", "checks")),
    ("W8", _remove("workflow/define/inputs.json")),
    ("W8", _remove("workflow/define/state.json")),
    ("W8", _remove("workflow/define/define-calls.jsonl")),
    ("W8", _remove("workflow/define/define-events.jsonl")),
    ("W8", _remove("workflow/define/feature-definition.md")),
    ("W9", _set("define", "ending", value="definition_unusable")),
    ("W10", _set("verify", "judged", value=[])),
    ("W10", _remove("workflow/judge/call-0.request.json")),
    ("W10", _remove("workflow/judge/call-0.response.json")),
    ("W10", _remove("workflow/judge/call-0.result.json")),
    ("W10", _remove("workflow/verify/base-tests.json")),
    ("W10", _drop("verify", "judge_calls")),
    ("W11", _set("integrity", value=[{"name": "session_log_removed", "detail": "x", "turn": 1}])),
]


def test_a_complete_trial_holds_every_row(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t, define=True, verify=True, judge=True)

    assert {c.status for c in _checks(t).values()} == {"pass"}


@pytest.mark.parametrize(("row", "change"), ROW_CASES)
def test_a_missing_required_artifact_of_an_enabled_stage_invalidates_the_trial(
    tmp_path: Path, row: str, change: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=True, verify=True, judge=True)
    _mutate(agent, change)

    checks = _checks(t)
    assert checks[row].status == "violation", checks[row].detail
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    evidence = next(c for c in health_validity(report) if c.name == "health_evidence")
    assert not evidence.passed
    assert row in evidence.detail


READINGS: list[tuple[str, Mutation]] = [
    ("W5", _remove("workflow/verify/hook-runs.jsonl")),
    ("W5", _set("verify", "hook_log_sha256", value="0" * 64)),
]


@pytest.mark.parametrize(("row", "change"), READINGS)
def test_a_missing_or_altered_hook_log_is_a_reading_that_never_invalidates(
    tmp_path: Path, row: str, change: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=True, verify=True, judge=True)
    _mutate(agent, change)

    checks = _checks(t)
    assert checks[row].status == "unmeasured", checks[row].detail
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    assert all(c.passed for c in health_validity(report))


def test_hook_log_findings_are_readings_but_session_log_findings_still_invalidate(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=True, verify=True, judge=True)
    forged = [{"name": "hook_log_rewritten", "detail": "x"}]
    _mutate(agent, _set("verify", "integrity", value=forged))

    assert _checks(t)["W11"].status == "pass"
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    assert all(c.passed for c in health_validity(report))
    g1 = next(c for c in report.checks if c.id == "G1")
    assert g1.status != "violation"
    assert g1.evidence["hook_log_findings"] == [f"{STEP}: hook_log_rewritten after ?"]

    _mutate(agent, _set("verify", "integrity", value=[*forged, {"name": "session_log_removed"}]))
    assert _checks(t)["W11"].status == "violation"


def test_deleting_every_record_is_invalid_because_the_trial_config_still_expects_them(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=True, verify=True, judge=True)
    for path in sorted(agent.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()

    violated = {i for i, c in _checks(t).items() if c.status == "violation"}
    assert violated == {"W1", "W3", "W6", "W8", "W10"}


def test_stages_the_config_does_not_enable_owe_nothing(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t, define=False, verify=False, judge=False)

    checks = _checks(t)
    assert {c.status for c in checks.values()} == {"pass"}
    assert "not applicable" in checks["W3"].detail
    assert "not applicable" in checks["W8"].detail


def test_a_stage_only_the_config_enables_is_owed_though_the_record_never_names_it(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=False, verify=True, judge=False)
    _mutate(agent, lambda a, w: (w.pop("verify"), w.update(stages=["build"])))

    checks = _checks(t)
    assert checks["W1"].status == "violation"
    assert checks["W3"].status == "violation"


def test_a_real_agent_run_without_a_readable_config_is_invalid_not_a_fallback(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t, define=True, verify=True, judge=True)
    (t.trial_dir / "config.json").unlink()

    checks = _checks(t)
    assert {i: c.status for i, c in checks.items() if i != "W5"} == dict.fromkeys(
        (i for i in checks if i != "W5"), "violation"
    )
    assert checks["W5"].status == "unmeasured"  # a reading, never a violation
    assert "applicability unknown" in checks["W1"].detail


def test_a_no_native_override_owes_no_workflow_evidence(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    (t.trial_dir / "config.json").write_text(json.dumps({"agent": {"name": "oracle"}}))
    (t.trial_dir / "steps" / STEP / "agent/workflow/workflow.json").unlink()

    checks = _checks(t)
    assert {c.status for c in checks.values()} == {"pass"}
    assert all("not applicable" in c.detail for c in checks.values())


def _refused_with_unusable_definition(agent: Path, workflow: Workflow) -> None:
    for name in (
        "state.json",
        "define-calls.jsonl",
        "define-events.jsonl",
        "feature-definition.md",
    ):
        (agent / "workflow/define" / name).unlink()
    workflow["define"] |= {"ending": "definition_unusable", "closed": True, "definition": None}
    workflow["define"]["definition_sha256"] = None
    workflow["verify"] |= {"ending": "gate_refused", "judged": [], "judge_calls": []}
    workflow["verify"]["gates"][0]["verdict"] = "refused"
    (agent / "workflow/verify/gate-1.json").write_text(json.dumps({"verdict": "refused"}))


def _cut(agent: Path, workflow: Workflow) -> None:
    workflow["verify"] |= {"ending": "deadline", "gates": [], "judged": [], "judge_calls": []}


def _refused_ready(agent: Path, workflow: Workflow) -> None:
    workflow["verify"]["ending"] = "gate_refused"


@pytest.mark.parametrize(
    "outcome", [_refused_with_unusable_definition, _cut, _refused_ready], ids=lambda f: f.__name__
)
def test_legitimate_terminal_agent_outcomes_leave_the_table_valid(
    tmp_path: Path, outcome: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, define=True, verify=True, judge=True)
    _mutate(agent, outcome)

    assert {c.status for c in _checks(t).values()} == {"pass"}
