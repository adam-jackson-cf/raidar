"""Family W: a locked primitive's missing, malformed or inconsistent lab-owned records invalidate
the trial, row by row. Rows are keyed by primitive contract id, never by stage name."""

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

PACKAGES = ("define", "verify")
BASE_PRIMITIVES = ("frame/1", "install/1", "runner/1")
CRITERIA_FILE = "stages/raidar_define/outputs/close/criteria/criteria.json"
DEFINITION_FILE = "stages/raidar_define/outputs/close/definition/feature-definition.md"
LAST_MESSAGE = "workflow/build-1/last-message.txt"


DECLARED = {
    "define": {"setup": True, "stop": True, "outputs": ["criteria", "definition"]},
    "verify": {"setup": False, "stop": False, "outputs": []},
}
"""What ``agent_config`` writes per locked package from its manifest (``raidar/define`` declares
setup, stop and the outputs ``criteria`` and ``definition``)."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _put(agent: Path, relative: str, text: str) -> str:
    path = agent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return _sha(text)


def _call(package: str, primitive: str, **changes: Any) -> Workflow:
    """The host's ``call/1`` record of one package call."""
    call: Workflow = {
        "schema": "call/1",
        "package": f"raidar/{package}",
        "primitive": primitive,
        "sequence": 1,
        "ran": True,
        "exit_code": 0,
        "signal": None,
        "timed_out": False,
        "overflow": False,
        "bounds_violation": None,
        "integrity_error": None,
    }
    return call | changes


def _invocation(package: str, primitive: str) -> Workflow:
    return {"call": _call(package, primitive), "bounds_violation": None, "state": {}}


def _output(agent: Path, name: str, relative: str, contract: str | None) -> Workflow:
    digest = _put(agent, relative, f"{name}\n")
    return {
        "path": f"/app/.raidar/{name}",
        "contract": contract,
        "sha256": digest,
        "bytes": len(name) + 1,
        "host_path": relative,
        "error": None,
    }


def _outputs(agent: Path) -> Workflow:
    """The copy record of each output ``raidar/define``'s manifest declares."""
    return {
        "criteria": _output(agent, "criteria", CRITERIA_FILE, "criteria/1"),
        "definition": _output(agent, "definition", DEFINITION_FILE, None),
    }


def _stop_entry(outputs: Workflow) -> Workflow:
    resume = {
        "action": "resume",
        "reason": "requested",
        "call": _invocation("define", "stop/1"),
        "status": "resume",
        "outputs": outputs,
    }
    done = resume | {"action": "done", "reason": "reported", "status": "done"}
    digests = {name: output["sha256"] for name, output in outputs.items()}
    run = {
        "label": "initial",
        "passed": True,
        "expected": {"stdout_sha256": "s" * 64, "outputs": digests},
        "actual": {"stdout_sha256": "s" * 64, "outputs": digests},
        "call": {},
    }
    return {
        "rounds": 1,
        "repaired": False,
        "closed": False,
        "carry": {},
        "tokens": {},
        "canary": [run],
        "decisions": [resume, done],
    }


def _gate_record(agent: Path) -> Workflow:
    _put(agent, "gate/gate-1.json", json.dumps({"verdict": "passed"}))
    _put(agent, "gate/hook-runs.jsonl", json.dumps({"verdict": "passed", "tree": "t"}) + "\n")
    return {
        "max_fix_rounds": 0,
        "base": "b",
        "hooks_path": "/hooks",
        "canary": {"verdict": "passed"},
        "setup_check": {"status": "passed", "checks": []},
        "integrity": [],
        "reaps": [],
        "pre_fix_snapshots": [],
        "hook_outputs": [],
        "gates": [
            {
                "verdict": "passed",
                "tree": "t",
                "attribution": "passed",
                "record": "gate/gate-1.json",
                "checks": [],
            }
        ],
        "errors": [],
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


def _judge_record(agent: Path, criteria_sha256: str) -> Workflow:
    settings, passed, calls = _judge_files(agent)
    _put(agent, "gate/baseline.json", "{}\n")
    return {
        "criteria": "define.criteria",
        "criteria_sha256": criteria_sha256,
        "model": "m",
        "judge": settings,
        "passes": 1,
        "max_passes": 3,
        "judged": [passed],
        "judge_calls": calls,
        "baseline": {"record": "gate/baseline.json"},
        "baseline_passed": [],
        "ending": "verified",
        "errors": [],
    }


def _reply_record(agent: Path) -> Workflow:
    digest = _put(agent, LAST_MESSAGE, "done\n")
    _put(agent, "workflow/build-1/reply.json", "{}\n")
    reply = {
        "after": "workflow/build-1",
        "kind": "first_build",
        "ok": True,
        "fault": None,
        "error": None,
        "dropped": [],
        "reported": [],
        "sha256": digest,
        "record": "workflow/build-1/reply.json",
    }
    return {
        "replies": [reply],
        "repairs": {"syntax": 0, "semantic": 0},
        "ac_tests": {},
        "adrs": [],
        "sources": None,
    }


def _definition(*, judge: bool, packages: tuple[str, ...]) -> Workflow:
    body: Workflow = {
        "schema": 2,
        "name": "define-build-verify",
        "build": {"prompt": "build %%%TASK%%%", "reply": "acceptance-reply/1" if judge else None},
        "stages": [{"use": f"raidar/{name}@1", "params": {}} for name in packages],
        "gate": {"max_fix_rounds": 0},
        "judge": None,
    }
    if judge:
        body["judge"] = {
            "criteria": "define.criteria",
            "model": "m",
            "closed_at": 0.8,
            "open_at": 0.3,
            "max_passes": 3,
            "rework": "rework %%%GATE%%% %%%PUSHBACK%%%",
        }
    return body


def _primitives(*extra: str) -> dict[str, str]:
    return dict.fromkeys((*BASE_PRIMITIVES, *extra), "d" * 64)


def _materialise(
    t: HealthyTrial, *, gate: bool = True, calls: bool = True, judge: bool = True
) -> Path:
    """The lab's config (definition, locked packages and primitives) and every record the locked
    primitives leave, as a real package trial would. Without ``calls`` the arm runs only the
    package that declares no setup, stop or outputs."""
    packages = PACKAGES if calls else ("verify",)
    extra = [
        *(["gate/1"] if gate else []),
        *(["setup/1", "stop/1", "outputs/1"] if calls else []),
        *(["jev-judge/1", "acceptance-reply/1"] if judge else []),
    ]
    locked = {
        f"raidar/{name}": {"release": f"raidar/{name}@1", "declared": DECLARED[name]}
        for name in packages
    }
    write_trial_config(
        t.trial_dir,
        _definition(judge=judge, packages=packages),
        locked_packages=locked,
        primitives=_primitives(*extra),
        pack="p",
    )
    agent = t.trial_dir / "steps" / STEP / "agent"
    path = agent / "workflow/workflow.json"
    workflow = json.loads(path.read_text())
    outputs = _outputs(agent)
    workflow |= {
        "frame/1": dict.fromkeys(packages, "fragment"),
        "install/1": {
            name: {"schema": "install/1", "reference": f"raidar/{name}", "package_digest": "a" * 64}
            for name in packages
        },
        "setup/1": {},
        "stop/1": {},
        "outputs/1": {},
    }
    if calls:
        workflow["setup/1"] = {
            "define": {**_invocation("define", "setup/1"), "outputs": {}},
        }
        workflow["stop/1"] = {"define": _stop_entry(outputs)}
        workflow["outputs/1"] = {"define": {**outputs, "cut": None}}
    if gate:
        workflow["gate/1"] = _gate_record(agent)
    if judge:
        workflow["jev-judge/1"] = _judge_record(agent, outputs["criteria"]["sha256"])
        workflow["acceptance-reply/1"] = _reply_record(agent)
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


def _set(*keys: Any, value: Any) -> Mutation:
    def change(agent: Path, workflow: Workflow) -> None:
        target: Any = workflow
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return change


def _drop(*keys: Any) -> Mutation:
    def change(agent: Path, workflow: Workflow) -> None:
        target: Any = workflow
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


def _reopened(agent: Path, workflow: Workflow) -> None:
    entry = workflow["stop/1"]["define"]
    resume, done = entry["decisions"]
    closed = resume | {"action": "closed", "reason": "closed by the package"}
    entry |= {"closed": True, "decisions": [resume, closed, done]}


def _claimed_done_without_criteria(agent: Path, workflow: Workflow) -> None:
    """The producer reported done, yet the judge saw no criteria: never an excused outcome."""
    workflow[JUDGE] |= {"ending": "no_criteria", "criteria_sha256": None, "judged": []}


def _deadline_without_a_cut(agent: Path, workflow: Workflow) -> None:
    workflow[JUDGE] |= {"ending": "deadline", "judged": [], "judge_calls": []}


def _sidecar(verdict: str) -> Mutation:
    return lambda agent, workflow: (agent / "gate/gate-1.json").write_text(
        json.dumps({"verdict": verdict})
    )


STOP = ("stop/1", "define")
OUTPUT = ("outputs/1", "define", "criteria")
GATE = "gate/1"
JUDGE = "jev-judge/1"
REPLY = "acceptance-reply/1"

ROW_CASES: list[tuple[str, Mutation]] = [
    ("W1", _remove("workflow/workflow.json")),
    ("W1", _drop("turns")),
    ("W1", _drop("frame/1")),
    ("W1", _drop("install/1")),
    ("W1", _set("install/1", "verify", "reference", value="raidar/other")),
    ("W1", _drop("setup/1")),
    ("W1", _drop("stop/1")),
    ("W1", _drop("outputs/1")),
    ("W1", _drop(GATE)),
    ("W1", _set(GATE, "errors", value=["GateInvalid: the tools changed"])),
    ("W1", _drop(JUDGE)),
    ("W1", _drop(REPLY)),
    ("W1", _set("invalid", value={"kind": "integrity", "detail": "x", "record": None})),
    ("W2", _rollout),
    ("W2", _empty_audit),
    ("W2", _bare_audit),
    ("W3", _remove("gate/gate-1.json")),
    ("W3", _set(GATE, "gates", value=[])),
    ("W3", _set(GATE, "gates", value=["torn"])),
    ("W3", _drop(GATE, "gates", 0, "attribution")),
    ("W3", _sidecar("refused")),
    ("W3", _drop(GATE)),
    ("W4", _set(GATE, "canary", "error", value="the canary printed no record")),
    ("W4", _set(GATE, "canary", "verdict", value="error")),
    ("W4", _set(GATE, "canary", value=None)),
    ("W4", _set(GATE, "gates", 0, "attribution", value="instrument_invalid")),
    ("W4", _set(GATE, "gates", 0, "attribution", value="integrity_invalid")),
    ("W4", _set(GATE, "errors", value=["TransportError: x"])),
    ("W6", _drop(GATE, "setup_check")),
    ("W6", _set(GATE, "setup_check", value={"checks": []})),
    ("W7", _set(GATE, "setup_check", value={"status": "failed", "checks": []})),
    ("W8", _drop("setup/1", "define", "call")),
    ("W8", _set("setup/1", "define", "call", "exit_code", value=1)),
    ("W8", _set("setup/1", "define", "call", "timed_out", value=True)),
    ("W8", _set("setup/1", "define", "call", "primitive", value="stop/1")),
    ("W8", _set("setup/1", "define", "call", "package", value="verify")),
    ("W8", _set("setup/1", "define", "call", "ran", value=False)),
    ("W8", _set("setup/1", "define", value="torn")),
    ("W8", _drop(*STOP, "decisions", 0, "call")),
    ("W8", _set(*STOP, "decisions", 1, "call", "call", "primitive", value="setup/1")),
    ("W8", _set(*STOP, "decisions", 1, "action", value="skipped")),
    ("W8", _drop(*STOP, "canary")),
    ("W8", _drop(*STOP, "decisions")),
    ("W8", _remove(CRITERIA_FILE)),
    ("W8", _set(*OUTPUT, "sha256", value="0" * 64)),
    ("W8", _set(*OUTPUT, "host_path", value="../../criteria.json")),
    ("W8", _set(*OUTPUT, "host_path", value="/var/lib/raidar/criteria.json")),
    ("W8", _set(*OUTPUT, "error", value="not a single regular file")),
    ("W8", _drop(*OUTPUT, "sha256")),
    ("W8", _drop("outputs/1")),
    ("W8", _drop("outputs/1", "define")),
    ("W8", _drop(*OUTPUT)),
    ("W8", _drop("outputs/1", "define", "definition")),
    ("W8", _remove(DEFINITION_FILE)),
    ("W8", _set(*STOP, "canary", 0, "expected", "outputs", value={"criteria": "x"})),
    ("W8", _drop("setup/1", "define")),
    ("W8", _drop("stop/1", "define")),
    ("W8", _set("outputs/1", "verify", value={"extra": {"error": None, "sha256": None}})),
    ("W8", _set(*OUTPUT[:2], "extra", value={"error": None, "sha256": None})),
    ("W9", _drop("stop/1", "define")),
    ("W9", _set(*STOP, "rounds", value=3)),
    ("W9", _set(*STOP, "closed", value=True)),
    ("W9", _set(*STOP, "repaired", value=True)),
    ("W9", _reopened),
    ("W9", _set(*STOP, "decisions", 1, "attribution", value="agent_output")),
    ("W9", _drop("stop/1")),
    ("W10", _set(JUDGE, "judged", value=[])),
    ("W10", _remove("workflow/judge/call-0.request.json")),
    ("W10", _remove("workflow/judge/call-0.response.json")),
    ("W10", _remove("workflow/judge/call-0.result.json")),
    ("W10", _remove("gate/baseline.json")),
    ("W10", _drop(JUDGE, "judge_calls")),
    ("W10", _drop(JUDGE, "judge")),
    ("W10", _set(JUDGE, "criteria_sha256", value="0" * 64)),
    ("W10", _set(JUDGE, "criteria", value="define.other")),
    ("W10", _set(REPLY, "replies", 0, "sha256", value="0" * 64)),
    ("W10", _remove(LAST_MESSAGE)),
    ("W10", _remove("workflow/build-1/reply.json")),
    ("W10", _set(REPLY, "replies", 0, "ok", value="yes")),
    ("W10", _set(REPLY, "replies", value=[])),
    ("W10", _drop(REPLY, "repairs")),
    ("W10", _drop(JUDGE)),
    ("W10", _claimed_done_without_criteria),
    ("W10", _deadline_without_a_cut),
    ("W10", _set(JUDGE, "errors", value=["define.criteria: the copied bytes drifted"])),
    ("W11", _set("integrity", value=[{"name": "session_log_removed", "detail": "x", "turn": 1}])),
    ("W11", _set(GATE, "integrity", value=[{"name": "tool_tree_changed", "detail": "x"}])),
]


def test_a_complete_trial_holds_every_row(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t)

    checks = _checks(t)
    assert {c.status for c in checks.values()} == {"pass"}
    assert not any("not applicable" in c.detail for c in checks.values())


@pytest.mark.parametrize(("row", "change"), ROW_CASES)
def test_a_missing_malformed_or_tampered_record_of_a_locked_primitive_invalidates_the_trial(
    tmp_path: Path, row: str, change: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t)
    _mutate(agent, change)

    checks = _checks(t)
    assert checks[row].status == "violation", checks[row].detail
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    evidence = next(c for c in health_validity(report) if c.name == "health_evidence")
    assert not evidence.passed
    assert row in evidence.detail


READINGS: list[tuple[str, Mutation]] = [
    ("W5", _remove("gate/hook-runs.jsonl")),
    ("W5", lambda agent, workflow: (agent / "gate/hook-runs.jsonl").write_text("{not json\n")),
    ("W5", lambda agent, workflow: (agent / "gate/hook-runs.jsonl").write_text("[1]\n")),
]


@pytest.mark.parametrize(("row", "change"), READINGS)
def test_a_missing_or_malformed_hook_log_is_a_reading_that_never_invalidates(
    tmp_path: Path, row: str, change: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t)
    _mutate(agent, change)

    checks = _checks(t)
    assert checks[row].status == "unmeasured", checks[row].detail
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    assert all(c.passed for c in health_validity(report))


def test_hook_log_findings_are_readings_but_session_log_findings_still_invalidate(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t)
    forged = [{"name": "hook_log_rewritten", "detail": "x"}]
    _mutate(agent, _set(GATE, "integrity", value=forged))

    assert _checks(t)["W11"].status == "pass"
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    assert all(c.passed for c in health_validity(report))

    _mutate(agent, _set(GATE, "integrity", value=[*forged, {"name": "session_log_removed"}]))
    assert _checks(t)["W11"].status == "violation"


def test_deleting_every_record_is_invalid_because_the_lock_still_owes_them(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t)
    for path in sorted(agent.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()

    violated = {i for i, c in _checks(t).items() if c.status == "violation"}
    assert violated == {"W1", "W3", "W4", "W6", "W8", "W9", "W10"}


APPLICABILITY: list[tuple[dict[str, bool], frozenset[str]]] = [
    ({"gate": False, "calls": False, "judge": False}, frozenset()),
    ({"gate": True, "calls": False, "judge": False}, frozenset({"W3", "W4", "W5", "W6", "W7"})),
    ({"gate": False, "calls": True, "judge": False}, frozenset({"W8", "W9"})),
    ({"gate": False, "calls": True, "judge": True}, frozenset({"W8", "W9", "W10"})),
]


@pytest.mark.parametrize(
    ("locked", "owed"), APPLICABILITY, ids=["baseline", "gate", "calls", "calls-and-judge"]
)
def test_a_row_is_owed_exactly_when_its_primitive_is_locked(
    tmp_path: Path, locked: dict[str, bool], owed: frozenset[str]
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, **locked)
    assert {c.status for c in _checks(t).values()} == {"pass"}

    def erase(_, workflow: Workflow) -> None:
        for primitive in ("gate/1", "setup/1", "stop/1", "outputs/1", JUDGE, REPLY):
            workflow.pop(primitive, None)
        (agent / "gate/hook-runs.jsonl").unlink(missing_ok=True)

    _mutate(agent, erase)

    affected = {i for i, c in _checks(t).items() if c.status != "pass"}
    # W7 evaluates a recorded setup result; W6 owns an absent result.
    assert affected - {"W1"} == owed - {"W7"}


def test_an_unlocked_primitive_leaves_no_row_owed_though_its_record_is_gone(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, gate=False, judge=False)
    _mutate(agent, _drop("outputs/1"))

    checks = _checks(t)
    assert checks["W3"].status == "pass"
    assert checks["W10"].status == "pass"
    assert checks["W8"].status == "violation"  # outputs/1 is locked: its record is owed


def test_a_stage_named_gate_or_judge_owes_nothing_unless_the_lock_says_so(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t, gate=False, calls=False, judge=False)
    _mutate(agent, _set("gate/1", value={"gates": []}))

    assert {c.status for c in _checks(t).values()} == {"pass"}


@pytest.mark.parametrize("kwarg", ["locked_packages", "primitives"])
def test_a_package_arm_missing_its_runner_kwargs_has_unknown_applicability(
    tmp_path: Path, kwarg: str
) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t)
    config = json.loads((t.trial_dir / "config.json").read_text())
    del config["agent"]["kwargs"][kwarg]
    (t.trial_dir / "config.json").write_text(json.dumps(config))

    checks = _checks(t)
    assert checks["W3"].status == "violation"  # unknown applicability owes every row
    assert "applicability unknown" in checks["W3"].detail


def _relock(t: HealthyTrial, change: Callable[[dict[str, Any]], None]) -> None:
    config = json.loads((t.trial_dir / "config.json").read_text())
    change(config["agent"]["kwargs"])
    (t.trial_dir / "config.json").write_text(json.dumps(config))


def _lock(name: str) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["primitives"].update({name: "d" * 64})


def _unlock(name: str) -> Callable[[dict[str, Any]], None]:
    def change(kwargs: dict[str, Any]) -> None:
        del kwargs["primitives"][name]

    return change


def _redefine(**changes: Any) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["definition"].update(changes)


def _undeclare(kwargs: dict[str, Any]) -> None:
    del kwargs["locked_packages"]["raidar/define"]["declared"]


UNKNOWN_LOCKS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("gate-version", _lock("gate/2")),
    ("judge-version", _lock("jev-judge/2")),
    ("stage-type", _lock("define")),
    ("no-judge-lock", _unlock("jev-judge/1")),
    ("no-reply-lock", _unlock("acceptance-reply/1")),
    ("undeclared-judge", _redefine(judge=None)),
    ("undeclared-reply", _redefine(judge=None, build={"prompt": "%%%TASK%%%", "reply": None})),
    ("stray-key", _redefine(stray=1)),
    ("stop-declared-unlocked", _unlock("stop/1")),
    ("no-declarations", _undeclare),
]


@pytest.mark.parametrize(
    "change", [c for _, c in UNKNOWN_LOCKS], ids=[name for name, _ in UNKNOWN_LOCKS]
)
def test_an_unknown_primitive_version_or_an_inconsistent_lock_fails_closed(
    tmp_path: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t)
    _relock(t, change)

    checks = _checks(t)
    assert all(c.status == "violation" for i, c in checks.items() if i != "W5")
    assert "applicability unknown" in checks["W10"].detail


@pytest.mark.parametrize(
    "broken",
    [
        {"schema": 2},
        {"schema": 2, "name": "x", "build": {"prompt": "no task token"}},
        {"schema": 2, "name": "x", "build": {"prompt": "%%%TASK%%%"}, "stray": 1},
        {"schema": 2, "name": "x", "build": {"prompt": "%%%TASK%%%"}, "stages": [{"use": "bad"}]},
        {
            "schema": 2,
            "name": "x",
            "build": {"prompt": "%%%TASK%%%", "reply": "acceptance-reply/1"},
            "judge": {"criteria": "bad"},
        },
    ],
    ids=["empty", "no-token", "stray-key", "bad-release", "bad-judge"],
)
def test_a_malformed_or_unreadable_definition_has_unknown_applicability(
    tmp_path: Path, broken: dict[str, Any]
) -> None:
    t = healthy_trial(tmp_path)
    write_trial_config(t.trial_dir, broken)

    checks = _checks(t)
    assert checks["W1"].status == "violation"
    assert checks["W3"].status == "violation"  # owed by no known primitive: only unknown owes it


def test_a_stage_keyed_definition_is_not_read_as_a_fallback(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    stages = {"build": {"prompt": "build %%%TASK%%%"}, "verify": {"enabled": True}}
    config = {"agent": {"kwargs": {"definition": {"stages": stages}}}}
    (t.trial_dir / "config.json").write_text(json.dumps(config))

    checks = _checks(t)
    assert all(c.status == "violation" for i, c in checks.items() if i != "W5")
    assert "applicability unknown" in checks["W3"].detail


def test_a_real_agent_run_without_a_readable_config_is_invalid_not_a_fallback(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    _materialise(t)
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


@pytest.mark.parametrize("evidence", [_remove("workflow/workflow.json"), _drop("turns"), _rollout])
def test_a_package_free_trial_still_owes_build_and_session_evidence(
    tmp_path: Path, evidence: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = t.trial_dir / "steps" / STEP / "agent"
    _mutate(agent, evidence)

    assert {c.status for c in _checks(t).values()} & {"violation"}


def _refused_canary(agent: Path, workflow: Workflow) -> None:
    """The real runtime shape: the canary gate refuses the starting workspace cleanly (the
    package's own check fails before the agent's work), with no error."""
    workflow[GATE]["canary"] = {"verdict": "refused", "checks": [], "error": None}


def _gate_refused(agent: Path, workflow: Workflow) -> None:
    workflow[GATE]["gates"][0] |= {"verdict": "refused", "attribution": "refused"}
    _sidecar("refused")(agent, workflow)


def _agent_error(agent: Path, workflow: Workflow) -> None:
    workflow[GATE]["gates"][0] |= {"verdict": "error", "attribution": "agent_error"}
    _sidecar("error")(agent, workflow)


def _cut(agent: Path, workflow: Workflow) -> None:
    workflow["endings"] = {"checkpoint": "deadline"}
    workflow[GATE]["gates"] = []
    workflow[REPLY]["replies"] = []
    workflow[JUDGE] |= {"ending": "deadline", "judged": [], "judge_calls": [], "baseline": None}


def _closed_define(agent: Path, workflow: Workflow) -> None:
    """The define package closed with no criteria: its outputs are absent and nothing is judged."""
    entry = workflow["stop/1"]["define"]
    closed = entry["decisions"][0] | {"action": "closed", "reason": "closed by the package"}
    entry |= {"rounds": 0, "closed": True, "decisions": [closed]}
    for name in ("criteria", "definition"):
        gone = {"sha256": None, "host_path": None, "error": "missing"}
        workflow["outputs/1"]["define"][name] |= gone
    workflow[JUDGE] |= {"ending": "no_criteria", "criteria_sha256": None, "judged": []}
    workflow[JUDGE] |= {"judge_calls": [], "baseline": None}


def _repaired_then_closed(agent: Path, workflow: Workflow) -> None:
    entry = workflow["stop/1"]["define"]
    canary = {"verdict": "passed"}
    base = entry["decisions"][0]
    repair = base | {"action": "repair", "reason": "x", "attribution": "agent_output"}
    closed = base | {"action": "closed", "reason": "unusable after repair: x"}
    repair["canary"] = closed["canary"] = canary
    entry |= {"rounds": 0, "repaired": True, "closed": True, "decisions": [repair, closed]}


@pytest.mark.parametrize(
    "outcome",
    [_gate_refused, _refused_canary, _agent_error, _cut, _closed_define, _repaired_then_closed],
    ids=lambda f: f.__name__,
)
def test_legitimate_terminal_agent_outcomes_leave_the_table_valid(
    tmp_path: Path, outcome: Mutation
) -> None:
    t = healthy_trial(tmp_path)
    agent = _materialise(t)
    _mutate(agent, outcome)

    checks = _checks(t)
    assert {c.status for c in checks.values()} == {"pass"}, {
        i: c.detail for i, c in checks.items() if c.status != "pass"
    }
