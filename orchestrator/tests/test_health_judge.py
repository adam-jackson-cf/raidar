"""Run-health decisions over parsed data: the judge family (J), the lab-owned evidence rows (W) a
locked primitive owes, and the audit's other families (V, H, L, P, T, D) over in-memory evidence.
Judge evidence must support closure; absence never implies a pass. Family W is decided from
in-memory workflow records and lab configuration (a handful of small agent-log files for the
digests the rows verify); the audit's checks read an ``Audit`` built in memory, with a few tiny
files only where a check reads the host (a stored tree, a liveness record, a sealed manifest).
The stored-trial seams are the slow tier's."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple, cast

import pytest

from raidar.contract.harbor_result import RESULT_FILE
from raidar.contract.snapshot import tree_digest
from raidar.contract.trial import (
    NOT_STARTED,
    CheckpointState,
    ExecutionValidity,
    SnapshotInfo,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.health.admission import (
    EMPTY_TREE_DIGEST,
    AdmissionError,
    SmokeJob,
    admit_jobs,
    identity,
)
from raidar.engine.health.audit import (
    DEADLINE_GRACE_S,
    check_h1,
    check_h3,
    check_h4,
    check_h5,
    check_h6,
    check_h_target,
    check_v1,
    check_v2,
    check_v3,
)
from raidar.engine.health.base import Audit
from raidar.engine.health.dependencies import (
    GRADER_LOG,
    check_d1,
    check_d2,
    local_modules,
    undeclared_modules,
)
from raidar.engine.health.evidence import (
    OVERRIDE,
    UNKNOWN,
    Step,
    agent_of,
    configured,
    evidence_checks,
    evidence_family,
    expected_from,
)
from raidar.engine.health.fault_candidates import (
    FAULT_CANDIDATES,
    append_fault_candidates,
    candidates,
)
from raidar.engine.health.judge import judge_family
from raidar.engine.health.liveness import LIVENESS_REPORT
from raidar.engine.health.model import HealthCheck, HealthReport, load_health, write_health
from raidar.engine.health.reaper import (
    REAP_REPORT,
    ReapReport,
    load_reap_report,
    write_reap_report,
)
from raidar.engine.health.records_manifest import RECORDS_MANIFEST
from raidar.engine.health.setup_script import (
    RESET_REPORT,
    reset_body,
    reset_check,
    write_setup_scripts,
)
from raidar.engine.health.signals import (
    check_l1,
    check_l2,
    check_p2,
    check_p3,
    check_t1,
    check_t2,
    check_t3,
    tool_status,
)
from raidar.engine.health.tool_tree import TOOL_HASHES
from raidar.engine.trial.collect import health_validity
from raidar.grading.dependency_pins import Unprovided
from raidar.grading.records import (
    PRE_AGENT_LEAK_FILE,
    RECORD_FILE,
    StepRecord,
    step_record_dir,
)
from raidar.grading.submission_deps import SubmissionDependencies
from tests.capability_support import CAPABILITIES, OTHER_HARNESS
from tests.health_support import (
    BASELINE_DEFINITION,
    LIMITS,
    START,
    STEP,
    TRIAL_ID,
    memory_audit,
    memory_job,
    memory_ledger,
    memory_result,
    memory_step,
    memory_trial,
    package_kwargs,
    package_records,
    package_turn,
)


def _write(root: Path, path: str, value: Any) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value))


def _step(root: Path, probability: float | None = 0.9) -> dict[str, Any]:
    status = (
        "closed"
        if probability is not None and probability >= 0.8
        else "open"
        if probability is not None and probability <= 0.3
        else "undecided"
    )
    result = (
        {
            "ok": probability is not None,
            "answers": {"aspect_asserted": {"type": "noul", "noul": probability}},
        }
        if probability is not None
        else {"ok": False, "reason": "timeout"}
    )
    _write(root, "request.json", {"questions": {"aspect_asserted": {"type": "noul"}}})
    if probability is not None:
        _write(root, "response.json", {"answers": result["answers"]})
    _write(root, "result.json", result)
    aspect = {"id": "F1.AC1.A1", "probability": probability, "status": status, "calls": [0]}
    criterion = {
        "id": "F1.AC1",
        "kind": "behaviour",
        "status": status,
        "failing": [],
        "tests": ["test_a.py::test_a"],
        "test_results": {"test_a.py::test_a": "passed"},
        "aspects": [aspect],
    }
    passed = {
        "pass": 1,
        "gate_verdict": "passed",
        "criteria": [criterion],
        "closed": [],
        "open": [],
        "undecided": [],
    }
    passed[status] = ["F1.AC1"]
    verify = {
        "ending": "verified" if status == "closed" else "pass_limit",
        "judge": {"open_at": 0.3, "closed_at": 0.8, "regression_needs_baseline": True},
        "judge_calls": [
            {
                "index": 0,
                "aspect": aspect["id"],
                "request_file": "request.json",
                "response_file": "response.json" if probability is not None else None,
                "result_file": "result.json",
                "result": result,
                "probability": probability,
            }
        ],
        "judged": [passed],
    }
    _save(root, verify)
    return verify


def _save(root: Path, verify: dict[str, Any]) -> None:
    _write(root, "workflow/workflow.json", {"stages": ["build"], "jev-judge/1": verify})


def _checks(root: Path) -> dict[str, Any]:
    return {c.id: c for c in judge_family([("checkpoint_1", root)])}


@pytest.mark.parametrize("probability", [0.9, 0.8, 0.3, 0.1, 0.5, None])
def test_recorded_answers_support_closed_open_or_undecided_readings(
    tmp_path: Path, probability: float | None
) -> None:
    _step(tmp_path, probability)
    assert {c.status for c in _checks(tmp_path).values()} == {"pass"}
    if probability is None or probability == 0.5:
        assert _checks(tmp_path)["J3"].evidence["undecided"] == {"checkpoint_1": ["F1.AC1.A1"]}


def test_missing_raw_response_is_unmeasured(tmp_path: Path) -> None:
    _step(tmp_path)
    (tmp_path / "response.json").unlink()
    assert _checks(tmp_path)["J1"].status == "unmeasured"


def test_falsified_interpreted_answer_is_a_violation(tmp_path: Path) -> None:
    _step(tmp_path)
    _write(
        tmp_path, "response.json", {"answers": {"aspect_asserted": {"type": "noul", "noul": 0.1}}}
    )
    assert _checks(tmp_path)["J1"].status == "violation"


def test_closed_aspect_cannot_hide_an_open_answer(tmp_path: Path) -> None:
    verify = _step(tmp_path, 0.1)
    verify["judged"][0]["criteria"][0]["aspects"][0]["status"] = "closed"
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"


def test_an_undecided_criterion_cannot_end_verified(tmp_path: Path) -> None:
    verify = _step(tmp_path, None)
    verify["ending"] = "verified"
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J3"].status == "violation"


def test_regression_closes_only_with_a_passing_mapped_baseline(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    criterion = verify["judged"][0]["criteria"][0]
    criterion.update(kind="regression", aspects=[])
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"
    verify["baseline_passed"] = criterion["tests"]
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "pass"


def test_an_unpassed_mapped_test_cannot_close_a_criterion(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judged"][0]["criteria"][0]["test_results"] = {"test_a.py::test_a": "skipped"}
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "violation"


def test_missing_pass_evidence_is_unmeasured(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify.pop("judged")
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J2"].status == "unmeasured"
    assert _checks(tmp_path)["J3"].status == "unmeasured"


def test_no_judge_means_no_j_family(tmp_path: Path) -> None:
    _write(tmp_path, "workflow/workflow.json", {"stages": ["build"]})
    assert judge_family([("checkpoint_1", tmp_path)]) == ()


def test_a_retained_attempt_cannot_disappear_from_the_judge_ledger(tmp_path: Path) -> None:
    _step(tmp_path)
    _write(tmp_path, "workflow/judge/w1/call-0001.result.json", {"ok": False})
    _write(tmp_path, "workflow/judge/w2/call-0001.result.json", {"ok": False})
    assert _checks(tmp_path)["J1"].status == "violation"


def test_a_retained_invalid_provider_body_is_evidence_not_a_missing_response(
    tmp_path: Path,
) -> None:
    verify = _step(tmp_path, None)
    call = verify["judge_calls"][0]
    call["response_file"] = "response.json"
    call["result"]["reason"] = "invalid_response"
    _write(tmp_path, "result.json", call["result"])
    (tmp_path / "response.json").write_text("not provider JSON")
    _save(tmp_path, verify)
    assert _checks(tmp_path)["J1"].status == "pass"


@pytest.mark.parametrize(
    "location",
    ["pass", "criteria", "aspects", "tests", "test_results"],
)
def test_malformed_retained_closure_is_unmeasured(tmp_path: Path, location: str) -> None:
    verify = _step(tmp_path)
    passed = verify["judged"][0]
    criterion = passed["criteria"][0]
    if location == "pass":
        verify["judged"] = [None]
    elif location == "criteria":
        passed["criteria"] = [None]
    elif location == "aspects":
        criterion["aspects"] = [None]
    else:
        criterion[location] = None
    _save(tmp_path, verify)
    checks = _checks(tmp_path)
    assert checks["J2"].status == "unmeasured"
    assert checks["J3"].status == "unmeasured"


# ---- family W: decisions over the lab-owned records, from parsed data -------------------------

Workflow = dict[str, Any]
Mutation = Callable[[Path, Workflow], None]

STOP = ("stop/1", "define")
OUTPUT = ("outputs/1", "define", "criteria")
GATE = "gate/1"
JUDGE = "jev-judge/1"
REPLY = "acceptance-reply/1"
HOOK_LOG = "gate/hook-runs.jsonl"
ALL_LOCKED: dict[str, bool] = {"gate": True, "calls": True, "judge": True}


def _workflow(agent: Path, **locked: bool) -> Workflow:
    """The ``workflow.json`` a package trial leaves (its files written under ``agent``)."""
    turns = [package_turn(agent)]
    return {"kind": "raidar-workflow", "stages": ["build"], "turns": turns} | package_records(
        agent, **locked
    )


def _config(**locked: bool) -> dict[str, Any]:
    return {"kwargs": package_kwargs(**locked)}


def _rows(
    agent: Path,
    workflow: Workflow | None,
    config: Any,
    override: str | None = None,
) -> dict[str, HealthCheck]:
    expected = expected_from(config, override)
    step = Step("checkpoint_1", agent, workflow, expected)
    return {c.id: c for c in evidence_checks([step], expected)}


def _validity(rows: dict[str, HealthCheck]) -> dict[str, bool]:
    report = HealthReport(trial_id="t", checks=tuple(rows.values()))
    return {c.name: c.passed for c in health_validity(report)}


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


def _hook_log(text: str) -> Mutation:
    return lambda agent, workflow: (agent / HOOK_LOG).write_text(text)


ROW_CASES: list[tuple[str, Mutation]] = [
    ("W1", _drop("turns")),
    ("W1", _set("turns", value=[])),
    ("W1", _set("stages", value=["verify"])),
    ("W1", _set("stages", value="build")),
    ("W1", _drop("frame/1")),
    ("W1", _drop("install/1")),
    ("W1", _set("install/1", "verify", "reference", value="raidar/other")),
    ("W1", _set("install/1", "verify", value="torn")),
    ("W1", _drop("setup/1")),
    ("W1", _drop("stop/1")),
    ("W1", _drop("outputs/1")),
    ("W1", _drop(GATE)),
    ("W1", _set(GATE, "errors", value=["GateInvalid: the tools changed"])),
    ("W1", _set(GATE, "gates", value=None)),
    ("W1", _drop(JUDGE)),
    ("W1", _drop(REPLY)),
    ("W1", _set("invalid", value={"kind": "integrity", "detail": "x", "record": None})),
    ("W2", _rollout),
    ("W2", _empty_audit),
    ("W2", _bare_audit),
    ("W2", _set("turns", value=["torn"])),
    ("W2", _set("turns", 0, "log_dir", value="../escape")),
    ("W2", _set("turns", 0, "log_dir", value="/abs")),
    ("W2", _set("turns", 0, "log_dir", value=None)),
    ("W2", _set("turns", 0, "session_audit", "sources", value=None)),
    ("W2", _set("turns", 0, "session_audit", "sources", 0, "sha256", value="0" * 64)),
    ("W2", _set("turns", 0, "session_audit", "sources", 0, "path", value="../../x")),
    ("W3", _remove("gate/gate-1.json")),
    ("W3", _set(GATE, "gates", value=[])),
    ("W3", _set(GATE, "gates", value=None)),
    ("W3", _set(GATE, "gates", value=["torn"])),
    ("W3", _drop(GATE, "gates", 0, "attribution")),
    ("W3", _sidecar("refused")),
    ("W3", _set(GATE, "gates", 0, "record", value="../gate-1.json")),
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
    ("W8", _set(*STOP, "decisions", 0, "call", value={"call": None, "bounds_violation": None})),
    ("W8", _set(*STOP, "decisions", 1, "call", "call", "primitive", value="setup/1")),
    ("W8", _set(*STOP, "decisions", 1, "action", value="skipped")),
    ("W8", _drop(*STOP, "canary")),
    ("W8", _set(*STOP, "canary", value=[])),
    ("W8", _drop(*STOP, "decisions")),
    ("W8", _remove("stages/raidar_define/outputs/close/criteria/criteria.json")),
    ("W8", _set(*OUTPUT, "sha256", value="0" * 64)),
    ("W8", _set(*OUTPUT, "host_path", value="../../criteria.json")),
    ("W8", _set(*OUTPUT, "host_path", value="/var/lib/raidar/criteria.json")),
    ("W8", _set(*OUTPUT, "error", value="not a single regular file")),
    ("W8", _drop(*OUTPUT, "sha256")),
    ("W8", _drop("outputs/1")),
    ("W8", _drop("outputs/1", "define")),
    ("W8", _drop(*OUTPUT)),
    ("W8", _drop("outputs/1", "define", "definition")),
    ("W8", _remove("stages/raidar_define/outputs/close/definition/feature-definition.md")),
    ("W8", _set(*STOP, "canary", 0, "expected", "outputs", value={"criteria": "x"})),
    ("W8", _set(*STOP, "canary", 0, "expected", value=None)),
    ("W8", _drop("setup/1", "define")),
    ("W8", _drop("stop/1", "define")),
    ("W8", _set("outputs/1", "verify", value={"extra": {"error": None, "sha256": None}})),
    ("W8", _set(*OUTPUT[:2], "extra", value={"error": None, "sha256": None})),
    ("W9", _drop("stop/1", "define")),
    ("W9", _drop(*STOP, "decisions")),
    ("W9", _set(*STOP, "decisions", value=["torn"])),
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
    ("W10", _set(JUDGE, "baseline", value=None)),
    ("W10", _drop(JUDGE, "judge_calls")),
    ("W10", _drop(JUDGE, "judge")),
    ("W10", _set(JUDGE, "criteria_sha256", value="0" * 64)),
    ("W10", _set(JUDGE, "criteria", value="define.other")),
    ("W10", _set(JUDGE, "criteria", value=None)),
    ("W10", _set(REPLY, "replies", 0, "sha256", value="0" * 64)),
    ("W10", _remove("workflow/build-1/last-message.txt")),
    ("W10", _remove("workflow/build-1/reply.json")),
    ("W10", _set(REPLY, "replies", 0, "after", value=None)),
    ("W10", _set(REPLY, "replies", 0, "ok", value="yes")),
    ("W10", _set(REPLY, "replies", value=[])),
    ("W10", _set(REPLY, "replies", value=None)),
    ("W10", _drop(REPLY, "repairs")),
    ("W10", _drop(JUDGE)),
    ("W10", _claimed_done_without_criteria),
    ("W10", _deadline_without_a_cut),
    ("W10", _set(JUDGE, "errors", value=["define.criteria: the copied bytes drifted"])),
    ("W11", _set("integrity", value=[{"name": "session_log_removed", "detail": "x", "turn": 1}])),
    ("W11", _set(GATE, "integrity", value=[{"name": "tool_tree_changed", "detail": "x"}])),
    ("W11", _set("turns", 0, "integrity", value=["session_log_touched"])),
]


def test_a_complete_package_trial_holds_every_row(tmp_path: Path) -> None:
    rows = _rows(tmp_path, _workflow(tmp_path), _config())

    assert {c.status for c in rows.values()} == {"pass"}
    assert not any("not applicable" in c.detail for c in rows.values())
    assert all(_validity(rows).values())


@pytest.mark.parametrize(("row", "change"), ROW_CASES)
def test_a_missing_malformed_or_tampered_record_of_a_locked_primitive_invalidates_the_trial(
    tmp_path: Path, row: str, change: Mutation
) -> None:
    workflow = _workflow(tmp_path)
    change(tmp_path, workflow)

    rows = _rows(tmp_path, workflow, _config())

    assert rows[row].status == "violation", rows[row].detail
    invalid = next(
        c
        for c in health_validity(HealthReport(trial_id="t", checks=tuple(rows.values())))
        if c.name == "health_evidence"
    )
    assert not invalid.passed
    assert row in invalid.detail


@pytest.mark.parametrize("workflow", [None, {}], ids=["unreadable", "empty"])
def test_a_missing_or_empty_workflow_record_is_a_violation(
    tmp_path: Path, workflow: Workflow | None
) -> None:
    rows = _rows(tmp_path / "gone", workflow, _config())

    assert rows["W1"].status == "violation"


@pytest.mark.parametrize(
    "log", [None, "{not json\n", "[1]\n"], ids=["missing", "unreadable", "malformed"]
)
def test_a_missing_or_malformed_hook_log_is_a_reading_that_never_invalidates(
    tmp_path: Path, log: str | None
) -> None:
    workflow = _workflow(tmp_path)
    if log is None:
        (tmp_path / HOOK_LOG).unlink()
    else:
        _hook_log(log)(tmp_path, workflow)

    rows = _rows(tmp_path, workflow, _config())

    assert rows["W5"].status == "unmeasured", rows["W5"].detail
    assert all(_validity(rows).values())


def test_hook_log_findings_are_readings_but_session_log_findings_still_invalidate(
    tmp_path: Path,
) -> None:
    workflow = _workflow(tmp_path)
    forged = [{"name": "hook_log_rewritten", "detail": "x"}]
    workflow[GATE]["integrity"] = forged

    rows = _rows(tmp_path, workflow, _config())
    assert rows["W11"].status == "pass"
    assert all(_validity(rows).values())

    workflow[GATE]["integrity"] = [*forged, {"name": "session_log_removed"}, "session_log_removed"]
    rows = _rows(tmp_path, workflow, _config())
    assert rows["W11"].status == "violation"


def test_deleting_every_record_is_invalid_because_the_lock_still_owes_them(
    tmp_path: Path,
) -> None:
    rows = _rows(tmp_path / "gone", None, _config())

    violated = {i for i, c in rows.items() if c.status == "violation"}
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
    workflow = _workflow(tmp_path, **locked)
    assert {c.status for c in _rows(tmp_path, workflow, _config(**locked)).values()} == {"pass"}

    for primitive in (GATE, "setup/1", "stop/1", "outputs/1", JUDGE, REPLY):
        workflow.pop(primitive, None)
    (tmp_path / HOOK_LOG).unlink(missing_ok=True)

    rows = _rows(tmp_path, workflow, _config(**locked))
    affected = {i for i, c in rows.items() if c.status != "pass"}
    # W7 evaluates a recorded setup result; W6 owns an absent result.
    assert affected - {"W1"} == owed - {"W7"}


def test_an_unlocked_primitive_leaves_no_row_owed_though_its_record_is_gone(
    tmp_path: Path,
) -> None:
    locked = {"gate": False, "calls": True, "judge": False}
    workflow = _workflow(tmp_path, **locked)
    del workflow["outputs/1"]

    rows = _rows(tmp_path, workflow, _config(**locked))

    assert rows["W3"].status == "pass"
    assert rows["W10"].status == "pass"
    assert rows["W8"].status == "violation"  # outputs/1 is locked: its record is owed


def test_a_stage_named_gate_or_judge_owes_nothing_unless_the_lock_says_so(
    tmp_path: Path,
) -> None:
    locked = {"gate": False, "calls": False, "judge": False}
    workflow = _workflow(tmp_path, **locked) | {"gate/1": {"gates": []}}

    rows = _rows(tmp_path, workflow, _config(**locked))

    assert {c.status for c in rows.values()} == {"pass"}


def _relock(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    config = _config()
    change(config["kwargs"])
    return config


def _lock(name: str) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["primitives"].update({name: "d" * 64})


def _unlock(name: str) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["primitives"].pop(name)


def _redefine(**changes: Any) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["definition"].update(changes)


def _undeclare(kwargs: dict[str, Any]) -> None:
    del kwargs["locked_packages"]["raidar/define"]["declared"]


def _declare(**changes: Any) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs["locked_packages"]["raidar/define"]["declared"].update(changes)


def _without(key: str) -> Callable[[dict[str, Any]], None]:
    return lambda kwargs: kwargs.pop(key)


def _unlist_package(kwargs: dict[str, Any]) -> None:
    del kwargs["locked_packages"]["raidar/define"]


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
    ("no-locked-packages", _without("locked_packages")),
    ("no-primitives", _without("primitives")),
    ("no-runner-primitive", _unlock("runner/1")),
    ("package-not-locked", _unlist_package),
    ("setup-not-boolean", _declare(setup="yes")),
    ("stop-not-boolean", _declare(stop=None)),
    ("outputs-not-a-list", _declare(outputs="criteria")),
    ("outputs-not-names", _declare(outputs=[1])),
]


@pytest.mark.parametrize(
    "change", [c for _, c in UNKNOWN_LOCKS], ids=[name for name, _ in UNKNOWN_LOCKS]
)
def test_an_unknown_primitive_version_or_an_inconsistent_lock_fails_closed(
    tmp_path: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    rows = _rows(tmp_path, _workflow(tmp_path), _relock(change))

    assert all(c.status == "violation" for i, c in rows.items() if i != "W5")
    assert rows["W5"].status == "unmeasured"  # a reading, never a violation
    assert "applicability unknown" in rows["W10"].detail


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
def test_a_malformed_definition_has_unknown_applicability(
    tmp_path: Path, broken: dict[str, Any]
) -> None:
    rows = _rows(tmp_path, _workflow(tmp_path), {"kwargs": {"definition": broken}})

    assert rows["W1"].status == "violation"
    assert rows["W3"].status == "violation"  # owed by no known primitive: only unknown owes it


@pytest.mark.parametrize(
    "agent",
    [
        None,
        {},
        {"kwargs": {"definition": {"stages": {"build": {"prompt": "build %%%TASK%%%"}}}}},
        {"kwargs": "torn"},
        {"name": "claude-code"},
    ],
    ids=["none", "empty", "stage-keyed", "torn-kwargs", "other-agent"],
)
def test_an_unreadable_or_unknown_configuration_is_invalid_not_a_fallback(
    tmp_path: Path, agent: dict[str, Any] | None
) -> None:
    rows = _rows(tmp_path, _workflow(tmp_path), agent)

    assert {i: c.status for i, c in rows.items() if i != "W5"} == dict.fromkeys(
        (i for i in rows if i != "W5"), "violation"
    )
    assert rows["W5"].status == "unmeasured"
    assert "applicability unknown" in rows["W1"].detail


@pytest.mark.parametrize(
    ("agent", "override"),
    [(None, "oracle"), ({"name": "oracle"}, None), ({"name": "nop"}, None)],
    ids=["record-override", "oracle", "nop"],
)
def test_a_no_native_override_owes_no_workflow_evidence(
    tmp_path: Path, agent: dict[str, Any] | None, override: str | None
) -> None:
    rows = _rows(tmp_path / "gone", None, agent, override)

    assert {c.status for c in rows.values()} == {"pass"}
    assert all("not applicable" in c.detail for c in rows.values())


PACKAGE_FREE = {"kwargs": {"definition": BASELINE_DEFINITION}}


def _package_free(agent: Path) -> Workflow:
    """What a no-package schema-2 run's ``PackageCheckpoint.record`` emits (no package)."""
    records = {name: {} for name in ("frame/1", "install/1", "setup/1", "stop/1", "outputs/1")}
    turns = [package_turn(agent)]
    return {"kind": "raidar-workflow", "stages": ["build"], "turns": turns} | records


def test_a_complete_package_free_trial_owes_only_build_and_session_evidence(
    tmp_path: Path,
) -> None:
    rows = _rows(tmp_path, _package_free(tmp_path), PACKAGE_FREE)

    owed = {i for i, c in rows.items() if "not applicable" not in c.detail}
    assert owed == {"W1", "W2", "W11"}
    assert {c.status for c in rows.values()} == {"pass"}


@pytest.mark.parametrize("evidence", [_drop("turns"), _rollout], ids=["turns", "rollout"])
def test_a_package_free_trial_still_owes_build_and_session_evidence(
    tmp_path: Path, evidence: Mutation
) -> None:
    workflow = _package_free(tmp_path)
    evidence(tmp_path, workflow)

    rows = _rows(tmp_path, workflow, PACKAGE_FREE)

    assert {c.status for c in rows.values()} & {"violation"}


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


def _no_stop_call(agent: Path, workflow: Workflow) -> None:
    """The package never called stop: the decision carries the host's ``no stop call`` reason."""
    entry = workflow["stop/1"]["define"]
    entry["decisions"][0] |= {"call": None, "reason": "no stop call"}
    entry["rounds"] = 0


def _bounded_stop_call(agent: Path, workflow: Workflow) -> None:
    """The stop call was refused by a bound: no call record, but the violation is named."""
    entry = workflow["stop/1"]["define"]
    entry["decisions"][0]["call"] = {"call": None, "bounds_violation": "stdout over the bound"}


@pytest.mark.parametrize(
    "outcome",
    [
        _gate_refused,
        _refused_canary,
        _agent_error,
        _cut,
        _closed_define,
        _repaired_then_closed,
        _no_stop_call,
        _bounded_stop_call,
    ],
    ids=lambda f: f.__name__,
)
def test_legitimate_terminal_agent_outcomes_leave_the_table_valid(
    tmp_path: Path, outcome: Mutation
) -> None:
    workflow = _workflow(tmp_path)
    outcome(tmp_path, workflow)

    rows = _rows(tmp_path, workflow, _config())

    assert {c.status for c in rows.values()} == {"pass"}, {
        i: c.detail for i, c in rows.items() if c.status != "pass"
    }


def test_many_failing_steps_are_one_violation(tmp_path: Path) -> None:
    expected = expected_from(_config(), None)
    steps = [Step(f"s{n}", tmp_path / "gone", None, expected) for n in range(5)]

    w1 = {c.id: c for c in evidence_checks(steps, expected)}["W1"]

    assert w1.status == "violation"


def test_applicability_is_decided_per_step_from_its_own_lock(tmp_path: Path) -> None:
    locked = expected_from(_config(), None)
    other = expected_from(_config(gate=False), None)
    workflow = _workflow(tmp_path)
    steps = [Step("a", tmp_path, workflow, locked), Step("b", tmp_path, workflow, other)]

    w3 = {c.id: c for c in evidence_checks(steps, locked)}["W3"]

    assert w3.evidence["steps"] == ["a"]


def test_the_agent_section_comes_from_config_json_else_result_json() -> None:
    config = {"agent": {"name": "oracle"}}
    result = {"config": {"agent": {"name": "nop"}}}

    assert agent_of(config, result) == {"name": "oracle"}
    assert agent_of(None, result) == {"name": "nop"}
    assert agent_of({"agent": "torn"}, result) == {"name": "nop"}
    assert agent_of(None, {"config": None}) is None
    assert agent_of(None, None) is None


def test_configured_reads_the_lab_config_then_the_result_config(tmp_path: Path) -> None:
    assert configured(tmp_path, None) is UNKNOWN
    assert configured(tmp_path, "oracle") is OVERRIDE
    (tmp_path / "config.json").write_text("{torn")
    (tmp_path / RESULT_FILE).write_text(json.dumps({"config": {"agent": {"name": "nop"}}}))
    assert configured(tmp_path, None) is OVERRIDE
    (tmp_path / "config.json").write_text(json.dumps({"agent": _config()}))
    assert configured(tmp_path, None).runs_workflow


def test_the_evidence_family_reads_each_attempted_steps_workflow_record(tmp_path: Path) -> None:
    agent = tmp_path / "steps" / "checkpoint_1" / "agent"
    (agent / "workflow").mkdir(parents=True)
    (agent / "workflow/workflow.json").write_text(json.dumps(_workflow(agent)))
    (tmp_path / "config.json").write_text(json.dumps({"agent": _config()}))

    rows = evidence_family(tmp_path, None, ["checkpoint_1"])
    gone = evidence_family(tmp_path, None, ["checkpoint_1", "checkpoint_2"])

    assert {c.status for c in rows} == {"pass"}
    assert {c.id for c in gone if c.status == "violation"} >= {"W1"}
    assert evidence_family(tmp_path, None, []) == ()


def test_unreadable_workflow_evidence_never_clears_the_trial(tmp_path: Path) -> None:
    agent = tmp_path / "steps" / "checkpoint_1" / "agent"
    (agent / "workflow").mkdir(parents=True)
    (agent / "workflow/workflow.json").write_text("[1]")
    (tmp_path / "config.json").write_text(json.dumps({"agent": _config()}))

    w1 = {c.id: c for c in evidence_family(tmp_path, None, ["checkpoint_1"])}["W1"]

    assert w1.status == "violation"
    peer = tmp_path / "peer"
    _step(peer)
    rows = judge_family([("checkpoint_1", agent), ("checkpoint_2", peer)])
    assert {row.id: row.status for row in rows} == {
        "J1": "unmeasured",
        "J2": "unmeasured",
        "J3": "unmeasured",
    }


# ---- the run-health audit (V, H, L, P, T, D) decided over evidence held in memory ----------------

PEER = "checkpoint_2"
TIMEOUT = "sandbox_timeout"
HEALTHY_REWARDS = memory_ledger().rewards
KILL = {"killed": ["42 1000 S node codex", "43 1000 S sleep 9999"], "residual": [], "error": None}


def _trial_dir(tmp_path: Path) -> Path:
    return tmp_path / "job" / "trials" / TRIAL_ID


def _audit(tmp_path: Path, **kwargs: Any) -> Audit:
    return memory_audit(_trial_dir(tmp_path), **kwargs)


def _graded(tmp_path: Path, ledger: StepRecord, **step_changes: Any) -> Audit:
    """An audit whose one step was graded as ``ledger`` and recorded with ``step_changes``."""
    record = memory_trial((memory_step(STEP, ledger, **step_changes),))
    return _audit(tmp_path, ledgers={STEP: ledger}, record=record)


def _two_steps(tmp_path: Path, **kwargs: Any) -> Audit:
    record = memory_trial((memory_step(STEP), memory_step(PEER, index=1)))
    return _audit(tmp_path, record=record, **kwargs)


def test_v_checks_without_a_graded_step_are_unmeasured(tmp_path: Path) -> None:
    audit = _audit(tmp_path, ledgers={STEP: None})

    assert [check(audit).status for check in (check_v1, check_v2, check_v3)] == ["unmeasured"] * 3


@pytest.mark.parametrize(
    ("infra", "changes", "status"),
    [
        (None, {}, "pass"),
        (TIMEOUT, {}, "pass"),
        ("grader_crash", {"checkpoint_state": CheckpointState.SOLVED}, "pass"),
        (TIMEOUT, {"checkpoint_state": CheckpointState.SOLVED}, "violation"),
        (TIMEOUT, {"rewards": {"solved": 1.0}}, "violation"),
    ],
)
def test_v1_a_timed_out_evaluator_is_never_counted_as_a_pass(
    tmp_path: Path, infra: str | None, changes: dict[str, Any], status: str
) -> None:
    ledger = memory_ledger(passed=infra is None, infra=infra)

    found = check_v1(_graded(tmp_path, ledger, **changes))

    assert found.status == status
    assert (found.evidence.get("steps") == [STEP]) is (status == "violation")


@pytest.mark.parametrize(
    ("ledger_changes", "step_changes", "agree"),
    [
        ({}, {}, True),
        ({}, {"rewards": None}, True),
        ({"summary": {"solved": False}}, {}, False),
        ({}, {"strict_pass_rate": 0.1}, False),
        ({}, {"rewards": {**HEALTHY_REWARDS, "strict_pass_rate": 0.1}}, False),
        ({}, {"rewards": {"solved": 0.0}}, False),
    ],
)
def test_v2_case_counts_must_agree_between_ledger_and_record(
    tmp_path: Path, ledger_changes: dict[str, Any], step_changes: dict[str, Any], agree: bool
) -> None:
    ledger = memory_ledger().model_copy(update=ledger_changes)

    found = check_v2(_graded(tmp_path, ledger, **step_changes))

    assert found.status == ("pass" if agree else "violation")
    if agree:
        assert found.evidence["cases"] == {STEP: len(ledger.grade.cases)}
    else:
        assert found.evidence["steps"] == [STEP]


def _stored_app(tmp_path: Path, text: str = "print('hello')\n") -> str:
    """The stored ``/app`` tree a ledger's default ``stored_path`` names; returns its digest."""
    app = _trial_dir(tmp_path) / "steps" / STEP / "artifacts" / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "main.py").write_text(text)
    return tree_digest(app)


def test_v3_the_evaluated_tree_must_equal_the_stored_snapshot(tmp_path: Path) -> None:
    audit = _graded(tmp_path, memory_ledger(digest=_stored_app(tmp_path)))
    assert check_v3(audit).status == "pass"

    _stored_app(tmp_path, "print('edited after grading')\n")

    assert check_v3(audit).status == "violation"


def test_v3_a_tree_that_is_not_stored_is_unmeasured_never_a_pass(tmp_path: Path) -> None:
    found = check_v3(_graded(tmp_path, memory_ledger()))

    assert found.status == "unmeasured"
    assert found.evidence["steps"] == [STEP]


@pytest.mark.parametrize("snapshot", [None, SnapshotInfo(path="steps/x", digest="e" * 64)])
def test_v3_a_record_snapshot_other_than_the_ledgers_is_a_violation(
    tmp_path: Path, snapshot: SnapshotInfo | None
) -> None:
    found = check_v3(_graded(tmp_path, memory_ledger(), snapshot=snapshot))

    assert found.status == "violation"
    assert found.evidence["steps"] == [STEP]


def test_h1_every_expected_record_file_is_present(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    expected = dict(audit.expected_files())
    step_files = (RECORD_FILE, REAP_REPORT, PRE_AGENT_LEAK_FILE)
    assert set(expected) == {RESULT_FILE, *(f"{STEP}/{name}" for name in step_files)}
    assert check_h1(audit).status == "violation"

    for path in expected.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert check_h1(audit).status == "pass"

    expected[f"{STEP}/{PRE_AGENT_LEAK_FILE}"].unlink()
    found = check_h1(audit)

    assert found.status == "violation"
    assert found.evidence["missing"] == [f"{STEP}/{PRE_AGENT_LEAK_FILE}"]


@pytest.mark.parametrize(
    "changes", [{"checkpoint_state": CheckpointState.NOT_ATTEMPTED}, {"termination": NOT_STARTED}]
)
def test_a_step_that_never_started_owes_no_record_and_is_not_graded(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    record = memory_trial((memory_step(STEP), memory_step(PEER, index=1, **changes)))
    ledgers = {STEP: memory_ledger(), PEER: memory_ledger(PEER)}

    audit = _audit(tmp_path, ledgers=ledgers, record=record)

    assert [step.step for step in audit.attempted] == [STEP]
    assert [step.step for step, _ in audit.graded()] == [STEP]
    assert {label.split("/")[0] for label, _ in audit.expected_files()} == {RESULT_FILE, STEP}


@pytest.mark.parametrize(
    ("job", "status", "missing"),
    [
        ({}, "unmeasured", None),
        ({"runner": {}}, "unmeasured", None),
        ({"effective_limits": "none"}, "unmeasured", None),
        (
            {"effective_limits": {**LIMITS, "agent_s": {"other": 900}}},
            "violation",
            "agent_s.baseline",
        ),
        (
            {"effective_limits": {k: v for k, v in LIMITS.items() if k != "verifier_s"}},
            "violation",
            "verifier_s",
        ),
        ({"effective_limits": LIMITS}, "pass", None),
    ],
)
def test_h3_effective_limits_must_be_recorded(
    tmp_path: Path, job: dict[str, Any], status: str, missing: str | None
) -> None:
    found = check_h3(_audit(tmp_path, job=job))

    assert found.status == status
    assert found.evidence.get("missing") == (None if missing is None else [missing])


class Phase(NamedTuple):
    check: Callable[[Audit], HealthCheck]
    span: str
    key: str
    multiplier: str
    limit: int


PHASE = pytest.mark.parametrize(
    "phase",
    [
        pytest.param(
            Phase(check_h4, "agent_s", "agent_s", "agent_timeout_multiplier", 900), id="H4"
        ),
        pytest.param(
            Phase(check_h5, "setup_s", "agent_setup_s", "agent_setup_timeout_multiplier", 600),
            id="H5",
        ),
        pytest.param(
            Phase(check_h6, "verify_s", "verifier_s", "verifier_timeout_multiplier", 1200), id="H6"
        ),
    ],
)


def _timed(tmp_path: Path, phase: Phase, seconds: float, limits: dict[str, Any] = LIMITS) -> Audit:
    result = memory_result(STEP, **{phase.span: seconds})
    return _audit(tmp_path, result=result, job=memory_job(effective_limits=limits))


@PHASE
def test_h4_to_h6_phase_wall_times_stay_within_the_effective_limits(
    tmp_path: Path, phase: Phase
) -> None:
    limit = phase.limit
    assert phase.check(_timed(tmp_path, phase, limit - 1)).status == "pass"
    assert phase.check(_timed(tmp_path, phase, limit + DEADLINE_GRACE_S - 1)).status == "pass"

    over = phase.check(_timed(tmp_path, phase, limit + DEADLINE_GRACE_S + 1))

    assert over.status == "violation"
    assert over.evidence["limit_s"] == limit


@PHASE
def test_h4_to_h6_limits_scale_with_the_recorded_multipliers(tmp_path: Path, phase: Phase) -> None:
    multipliers = {"timeout_multiplier": 2.0, phase.multiplier: 1.5}
    limits = {**LIMITS, "multipliers": multipliers}

    found = phase.check(_timed(tmp_path, phase, 2 * phase.limit, limits))

    assert found.status == "pass"
    assert found.evidence["limit_s"] == phase.limit * 1.5 * 2.0


@PHASE
@pytest.mark.parametrize("shape", ["absent", "null", "text"])
def test_h4_to_h6_an_unrecorded_limit_is_unmeasured_not_a_pass(
    tmp_path: Path, phase: Phase, shape: str
) -> None:
    if shape == "absent":
        limits = {k: v for k, v in LIMITS.items() if k != phase.key}
    else:
        limits = {**LIMITS, phase.key: {"baseline": None} if shape == "null" else "soon"}

    assert phase.check(_timed(tmp_path, phase, phase.limit * 10, limits)).status == "unmeasured"


@PHASE
def test_h4_to_h6_a_phase_without_timing_in_result_json_is_unmeasured(
    tmp_path: Path, phase: Phase
) -> None:
    assert phase.check(_audit(tmp_path, result={})).status == "unmeasured"


def test_h_target_mirrors_the_guards_target_reachable(tmp_path: Path) -> None:
    lost = ValidityCheck(name="target_reachable", passed=False, detail="daemon down")
    record = memory_trial((memory_step(),), validity=ExecutionValidity(checks=(lost,)))

    found = check_h_target(_audit(tmp_path, record=record))

    assert (found.status, "daemon down" in found.detail) == ("violation", True)
    assert check_h_target(_audit(tmp_path)).status == "pass"
    unrecorded = _audit(tmp_path, job=memory_job(execution_target=None))
    assert check_h_target(unrecorded).status == "unmeasured"


def _window(job: Path, trial: str, start_s: int, end_s: int, *, under: str = "trials") -> None:
    """A trial directory under the job holding only the span its ``result.json`` records."""
    path = job / under / trial
    path.mkdir(parents=True, exist_ok=True)
    span = {
        "started_at": (START + timedelta(seconds=start_s)).isoformat(),
        "finished_at": (START + timedelta(seconds=end_s)).isoformat(),
    }
    (path / RESULT_FILE).write_text(json.dumps(span))


def test_p2_carries_the_configured_and_effective_concurrency(tmp_path: Path) -> None:
    found = check_p2(_audit(tmp_path))

    assert (found.status, found.evidence["configured"], found.evidence["effective"]) == (
        "pass",
        1,
        1,
    )


@pytest.mark.parametrize(
    ("job", "status"),
    [
        (memory_job(concurrency=None, execution_target=None), "unmeasured"),
        (memory_job(execution_target="down"), "pass"),
        (memory_job(concurrency=None), "pass"),
    ],
)
def test_p2_needs_a_recorded_concurrency(tmp_path: Path, job: dict[str, Any], status: str) -> None:
    assert check_p2(_audit(tmp_path, job=job)).status == status


def test_p2_peak_counts_the_trials_open_during_this_one(tmp_path: Path) -> None:
    job = tmp_path / "job"
    _window(job, TRIAL_ID, 0, 100)
    _window(job, "overlapping", 50, 150)
    _window(job, "later", 200, 300)

    assert check_p2(_audit(tmp_path)).evidence["peak_concurrent_trials"] == 2


def test_p2_peak_is_unmeasured_when_a_sibling_window_cannot_be_read(tmp_path: Path) -> None:
    job = tmp_path / "job"
    _window(job, TRIAL_ID, 0, 100)
    (job / "trials" / "torn").mkdir()
    (job / "trials" / "torn" / RESULT_FILE).write_text("{torn")

    assert check_p2(_audit(tmp_path)).evidence["peak_concurrent_trials"] is None


def test_p2_peak_is_unmeasured_for_a_trial_without_a_window(tmp_path: Path) -> None:
    _window(tmp_path / "job", "sibling", 0, 100)
    elsewhere = tmp_path / "job" / "scratch" / TRIAL_ID
    unwritten = memory_audit(elsewhere)
    assert check_p2(unwritten).evidence["peak_concurrent_trials"] is None

    _window(tmp_path / "job", TRIAL_ID, 0, 100, under="scratch")

    assert check_p2(unwritten).evidence["peak_concurrent_trials"] is None


@pytest.mark.parametrize(
    ("job", "override", "warnings"),
    [
        (memory_job(), None, 0),
        (memory_job(runner={"dirty": True}), None, 1),
        (memory_job(execution_target={"name": "mbp", "docker": None}), None, 1),
        (memory_job(admission={"overridden": True}), None, 1),
        (memory_job(), "oracle", 1),
        (memory_job(runner={"dirty": True}, admission={"overridden": True}), "oracle", 3),
    ],
)
def test_p3_collects_the_comparison_warnings(
    tmp_path: Path, job: dict[str, Any], override: str | None, warnings: int
) -> None:
    record = memory_trial((memory_step(),), agent_override=override)

    found = check_p3(_audit(tmp_path, job=job, record=record))

    assert found.status == "pass"
    assert len(found.evidence["warnings"]) == warnings


def test_p3_without_runner_provenance_is_unmeasured(tmp_path: Path) -> None:
    job = {k: v for k, v in memory_job().items() if k != "runner"}

    assert check_p3(_audit(tmp_path, job=job)).status == "unmeasured"


def _phase(decision: str, **extra: Any) -> dict[str, Any]:
    base = {"step": STEP, "decision": decision, "sample_count": 5, "unmeasured_samples": 0}
    return {**base, "reason": "no output, agent CPU or network for 60s", **extra}


def _liveness(tmp_path: Path, *phases: Any) -> Audit:
    trial = _trial_dir(tmp_path)
    trial.mkdir(parents=True, exist_ok=True)
    (trial / LIVENESS_REPORT).write_text(json.dumps({"phases": list(phases)}))
    return _audit(tmp_path)


@pytest.mark.parametrize(
    ("phases", "l1", "l2"),
    [
        ([_phase("active")], "pass", "pass"),
        ([_phase("idle_observed", first_idle_at_s=61.0)], "pass", "pass"),
        ([_phase("idle_killed", idle_for_s=600.0, kill=KILL)], "pass", "pass"),
        ([_phase("kill_failed", kill=KILL)], "pass", "violation"),
        ([_phase("active", sample_count=3, unmeasured_samples=3)], "pass", "unmeasured"),
        ([_phase("active", error="probe crashed")], "pass", "unmeasured"),
        (["junk", _phase("active")], "pass", "pass"),
        ([{**_phase("active"), "step": PEER}], "unmeasured", "pass"),
        ([], "unmeasured", "unmeasured"),
    ],
)
def test_l1_and_l2_read_the_liveness_of_each_agent_phase(
    tmp_path: Path, phases: list[Any], l1: str, l2: str
) -> None:
    audit = _liveness(tmp_path, *phases)

    assert (check_l1(audit).status, check_l2(audit).status) == (l1, l2)


@pytest.mark.parametrize("text", [None, "{torn", "[]"])
def test_l1_and_l2_without_a_readable_liveness_record_are_unmeasured(
    tmp_path: Path, text: str | None
) -> None:
    trial = _trial_dir(tmp_path)
    trial.mkdir(parents=True)
    if text is not None:
        (trial / LIVENESS_REPORT).write_text(text)
    audit = _audit(tmp_path)

    assert (check_l1(audit).status, check_l2(audit).status) == ("unmeasured", "unmeasured")


def test_l2_idle_findings_pass_and_carry_the_phase_and_the_killed_pids(tmp_path: Path) -> None:
    observed = check_l2(_liveness(tmp_path, _phase("idle_observed", first_idle_at_s=61.0)))
    assert observed.status == "pass"
    assert [f["decision"] for f in observed.evidence["findings"]] == ["idle_observed"]

    killed = _phase("idle_killed", first_idle_at_s=61.0, idle_for_s=600.0, kill=KILL)
    (finding,) = check_l2(_liveness(tmp_path, killed)).evidence["findings"]

    assert (finding["decision"], finding["idle_for_s"]) == ("idle_killed", 600.0)
    assert finding["killed_pids"] == ["42", "43"]


def _hash_point(point: str, sha: str | None, **fields: Any) -> dict[str, Any]:
    base = {"point": point, "present": sha is not None, "sha256": sha, "measured": True}
    return {**base, "step": STEP, **fields}


BASELINE = _hash_point("baseline", "a" * 64)
UNCHANGED = _hash_point("after_agent", "a" * 64)
CHANGED = _hash_point("after_agent", "b" * 64)
LOST = _hash_point("after_agent", "x", measured=False, unmeasured="boom")


@pytest.mark.parametrize(
    ("points", "status"),
    [
        ([], "unmeasured"),
        ([LOST], "unmeasured"),
        ([{**BASELINE, "measured": False}, UNCHANGED], "unmeasured"),
        ([BASELINE], "unmeasured"),
        ([BASELINE, LOST], "unmeasured"),
        ([BASELINE, UNCHANGED], "pass"),
        ([BASELINE, UNCHANGED, CHANGED], "violation"),
        ([BASELINE, CHANGED], "violation"),
        ([_hash_point("baseline", None), _hash_point("after_agent", None)], "pass"),
        ([_hash_point("baseline", None), _hash_point("after_agent", "c" * 64)], "violation"),
    ],
)
def test_t1_the_tool_tree_must_not_change_after_its_baseline_hash(
    points: list[dict[str, Any]], status: str
) -> None:
    found, _, evidence = tool_status(points)

    assert found == status
    assert (evidence.get("steps") == [STEP]) is (status == "violation")


def test_t1_reads_the_hashes_the_trial_took(tmp_path: Path) -> None:
    trial = _trial_dir(tmp_path)
    trial.mkdir(parents=True)
    audit = _audit(tmp_path)
    assert check_t1(audit).status == "unmeasured"

    points = [BASELINE, "junk", CHANGED]
    (trial / TOOL_HASHES).write_text(json.dumps({"points": points}))

    assert check_t1(audit).status == "violation"


@pytest.mark.parametrize(
    "findings",
    [[], ["forged.txt added"], [f"changed-{n}.txt changed" for n in range(21)]],
)
def test_t2_the_sealed_records_manifest_must_verify(tmp_path: Path, findings: list[str]) -> None:
    audit = _audit(tmp_path, job=memory_job(records_manifests={TRIAL_ID: "sealed"}))

    found = check_t2(audit, read_findings=lambda *_: findings)

    assert found.status == ("violation" if findings else "pass")
    if findings:
        assert found.evidence["differences"] == findings[:20]
        assert found.detail == "; ".join(findings[:20])


@pytest.mark.parametrize("stored", [None, b"{}\n"])
def test_an_absent_or_changed_records_manifest_is_a_tampering_violation(
    tmp_path: Path, stored: bytes | None
) -> None:
    if stored is not None:
        (tmp_path / RECORDS_MANIFEST).write_bytes(stored)
    audit = memory_audit(tmp_path, job=memory_job(records_manifests={TRIAL_ID: "0" * 64}))

    assert check_t2(audit).status == "violation"


@pytest.mark.parametrize("finished", [False, True])
def test_t2_without_a_sealed_digest_is_unmeasured(tmp_path: Path, finished: bool) -> None:
    job = memory_job(**({"finished_at": "2026-01-01T00:00:00+00:00"} if finished else {}))

    assert check_t2(_audit(tmp_path, job=job)).status == "unmeasured"


def _reap(tmp_path: Path, step: str, **fields: Any) -> None:
    report = ReapReport("agent", (), tuple(fields.get("residual", ())), fields.get("error"))
    write_reap_report(step_record_dir(_trial_dir(tmp_path), step), report)


@pytest.mark.parametrize(
    ("reports", "status"),
    [
        ({STEP: {}, PEER: {}}, "pass"),
        ({STEP: {}, PEER: {"residual": ["5 sleep"]}}, "violation"),
        ({STEP: {}, PEER: {"error": "reap refused"}}, "violation"),
        ({STEP: {}}, "unmeasured"),
        ({STEP: {"residual": ["5 sleep"]}}, "violation"),
        ({}, "unmeasured"),
    ],
)
def test_t3_a_reaper_residual_is_a_violation_and_a_missing_report_unmeasured(
    tmp_path: Path, reports: dict[str, dict[str, Any]], status: str
) -> None:
    for step, fields in reports.items():
        _reap(tmp_path, step, **fields)

    assert check_t3(_two_steps(tmp_path)).status == status


def test_t3_a_trial_with_no_attempted_step_has_no_reap_to_check(tmp_path: Path) -> None:
    never = memory_step(STEP, checkpoint_state=CheckpointState.NOT_ATTEMPTED)

    audit = _audit(tmp_path, record=memory_trial((never,)))

    assert check_t3(audit).status == "unmeasured"


def _with_deps(
    ledger: StepRecord,
    *,
    unprovided: tuple[tuple[str, str], ...] = (),
    declared: tuple[str, ...] = (),
) -> StepRecord:
    found = tuple(Unprovided(requirement=req, reason=why) for req, why in unprovided)
    deps = SubmissionDependencies(declared=declared, unprovided=found)
    return ledger.model_copy(update={"submission_dependencies": deps})


def test_d1_lists_each_measured_steps_unprovided_requirements(tmp_path: Path) -> None:
    clean = _with_deps(memory_ledger())
    gapped = _with_deps(memory_ledger(PEER), unprovided=(("numpy", "no-wheel"),))
    audit = _two_steps(tmp_path, ledgers={STEP: clean, PEER: gapped})

    found = check_d1(audit)

    assert found.status == "pass"  # a reading: it never invalidates a trial
    assert found.evidence["steps"] == {
        STEP: [],
        PEER: [{"requirement": "numpy", "reason": "no-wheel"}],
    }
    assert found.evidence["unmeasured"] == []


def test_d_readings_keep_a_step_without_dependency_evidence_unmeasured(tmp_path: Path) -> None:
    ledgers = {STEP: _with_deps(memory_ledger()), PEER: memory_ledger(PEER)}

    found = check_d1(_two_steps(tmp_path, ledgers=ledgers))

    assert found.status == "pass"
    assert found.evidence["unmeasured"] == [PEER]


def test_d_readings_without_any_measured_step_are_unmeasured(tmp_path: Path) -> None:
    ungraded = _audit(tmp_path, ledgers={STEP: None})
    never = memory_trial((memory_step(STEP, checkpoint_state=CheckpointState.NOT_ATTEMPTED),))
    unattempted = _audit(tmp_path, record=never)

    statuses = [check(a).status for a in (ungraded, unattempted) for check in (check_d1, check_d2)]

    assert statuses == ["unmeasured"] * 4


def _grader_log(tmp_path: Path, text: str) -> None:
    log = step_record_dir(_trial_dir(tmp_path), STEP) / GRADER_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text)


def test_d2_an_undeclared_import_is_heuristic_until_the_stored_tree_was_read(
    tmp_path: Path,
) -> None:
    _grader_log(tmp_path, "ModuleNotFoundError: No module named 'numpy'\n")
    audit = _audit(tmp_path, ledgers={STEP: _with_deps(memory_ledger())})
    assert check_d2(audit).evidence["steps"][STEP] == [{"module": "numpy", "basis": "heuristic"}]

    _stored_app(tmp_path)
    assert check_d2(audit).evidence["steps"][STEP] == [{"module": "numpy", "basis": "confirmed"}]

    (_trial_dir(tmp_path) / "steps" / STEP / "artifacts" / "app" / "numpy.py").write_text("")
    assert check_d2(audit).evidence["steps"][STEP] == []


def test_d2_a_step_without_a_readable_grader_log_is_unmeasured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = _audit(tmp_path, ledgers={STEP: _with_deps(memory_ledger())})
    assert check_d2(audit).status == "unmeasured"
    _grader_log(tmp_path, "ModuleNotFoundError: No module named 'numpy'\n")
    read = Path.read_text

    def unreadable(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name == GRADER_LOG:
            raise PermissionError(self.name)
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)

    assert check_d2(audit).status == "unmeasured"


def _hc(check_id: str, status: str, **evidence: Any) -> HealthCheck:
    return HealthCheck(
        id=check_id,
        family=cast(Any, check_id[0]),
        status=cast(Any, status),
        detail=f"{check_id} detail",
        evidence=evidence,
    )


def _health(*checks: HealthCheck) -> HealthReport:
    return HealthReport(trial_id=TRIAL_ID, checks=checks)


def test_a_violation_is_a_fault_candidate_per_step_or_once_without_steps() -> None:
    report = _health(
        _hc("H4", "violation", steps=[STEP, PEER]),
        _hc("T2", "violation"),
        _hc("V1", "pass"),
        _hc("L1", "unmeasured"),
    )

    lines = candidates(report)

    assert [(line["check_id"], line.get("step")) for line in lines] == [
        ("H4", STEP),
        ("H4", PEER),
        ("T2", None),
    ]
    assert {(line["state"], line["trial"]) for line in lines} == {("provisional", TRIAL_ID)}


def test_only_liveness_idle_findings_are_fault_candidates_beyond_violations() -> None:
    findings = [
        {"step": STEP, "decision": "idle_observed", "reason": "quiet"},
        {"step": PEER, "decision": "idle_killed", "reason": "quiet", "killed_pids": ["42"]},
        {"step": "checkpoint_3", "decision": "active", "reason": "busy"},
    ]

    report = _health(_hc("L2", "pass", findings=findings), _hc("P3", "pass", findings=findings))

    lines = candidates(report)

    assert [(line["step"], line["status"]) for line in lines] == [
        (STEP, "idle_observed"),
        (PEER, "idle_killed"),
    ]
    assert "42" in lines[1]["detail"]


def test_appending_fault_candidates_never_duplicates_a_trial_step_check_and_status(
    tmp_path: Path,
) -> None:
    path = tmp_path / FAULT_CANDIDATES
    assert append_fault_candidates(tmp_path, _health(_hc("V1", "pass"))) == []
    assert not path.exists()
    path.write_text("{torn\n")
    first = _health(_hc("H4", "violation", steps=[STEP]))

    written = append_fault_candidates(tmp_path, first)
    again = append_fault_candidates(tmp_path, first)
    extended = append_fault_candidates(tmp_path, _health(*first.checks, _hc("T2", "violation")))

    assert (len(written), again, [line["check_id"] for line in extended]) == (1, [], ["T2"])
    stored = [json.loads(line) for line in path.read_text().splitlines()[1:]]
    assert [line["check_id"] for line in stored] == ["H4", "T2"]


@pytest.mark.parametrize(
    ("report", "passed"),
    [
        (None, False),
        ("{torn", False),
        ({"error": "refused"}, False),
        ({"residual": ["5 sleep"]}, False),
        ({"state": [{"path": "/home/agent/.codex", "reset_failed": True}]}, False),
        ({"killed": ["7 sleep"], "residual": [], "state": [{"path": "/x"}]}, True),
    ],
)
def test_a_step_reset_passes_only_when_it_ran_and_left_no_process_or_state(
    tmp_path: Path, report: Any, passed: bool
) -> None:
    if report is not None:
        text = report if isinstance(report, str) else json.dumps(report)
        (tmp_path / RESET_REPORT).write_text(text)

    assert reset_check(tmp_path).passed is passed


def test_setup_scripts_need_a_step_and_never_overwrite_a_task_script(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one step"):
        write_setup_scripts(tmp_path, [])
    owned = tmp_path / "steps" / STEP / "workdir" / "setup.sh"
    owned.parent.mkdir(parents=True)
    owned.write_text("task's own")

    with pytest.raises(FileExistsError):
        write_setup_scripts(tmp_path, [STEP])

    assert owned.read_text() == "task's own"


def test_a_reset_script_without_the_state_targets_marker_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = SimpleNamespace(read_text=lambda encoding: "#!/bin/sh\n")
    fake = SimpleNamespace(joinpath=lambda name: script)
    monkeypatch.setattr("raidar.engine.health.setup_script.files", lambda package: fake)

    with pytest.raises(ValueError, match="@STATE_TARGETS@"):
        reset_body()


def test_a_reap_report_that_is_not_an_object_is_unreadable(tmp_path: Path) -> None:
    assert load_reap_report(tmp_path) is None
    (tmp_path / REAP_REPORT).write_text("[1]")

    assert load_reap_report(tmp_path) is None


DIGEST_A, DIGEST_B = "a" * 64, "b" * 64
RUNNER = {"toolchain_sha256": "t" * 64, "execution_sha256": "e" * 64}
RECIPES = {"pack": "p" * 64}
TARGET = {"name": "mbp", "docker": {"ncpu": 4}}
CLEAN_HEALTH = HealthReport(trial_id=TRIAL_ID, checks=())


def _agent(name: str = "luna", **changes: Any) -> dict[str, Any]:
    agent = {"agent": name, "digest": name, "model": "gpt-6", "reasoning": "low"}
    return {**agent, "capabilities": CAPABILITIES, **changes}


def _lock(kind: str, *agents: dict[str, Any], varies: tuple[str, ...] = ()) -> dict[str, Any]:
    arms = {f"arm-{agent['agent']}": agent for agent in agents or (_agent(),)}
    design: dict[str, Any] = {"kind": kind, **({"varies": list(varies)} if varies else {})}
    return {"execution": {"design": design, "arms": arms}}


def _wanted(*agents: dict[str, Any], varies: tuple[str, ...] = ()) -> dict[str, Any]:
    found = identity(_lock("ab-test", *agents, varies=varies), RUNNER, RECIPES, TARGET)
    assert found is not None
    return found


def _smoke_record(
    arm: str = "arm-luna", *, digests: tuple[str | None, ...] = (DIGEST_A,), **changes: Any
) -> TrialRecord:
    """A trial whose step ``i`` left ``/app`` at ``digests[i]`` (``None``: no snapshot)."""
    steps = tuple(
        memory_step(
            f"checkpoint_{i + 1}",
            index=i,
            snapshot=None if digest is None else SnapshotInfo(path="app", digest=digest),
            parent_snapshot_digest=digests[i - 1] if i else None,
        )
        for i, digest in enumerate(digests)
    )
    record = memory_trial(steps, **changes)
    return record.model_copy(update={"trial": record.trial.model_copy(update={"arm": arm})})


def _smoke(
    trial: TrialRecord | str,
    *,
    name: str = "exp/smoke",
    manifest: dict[str, Any] | None = None,
    without: tuple[str, ...] = (),
    lock: dict[str, Any] | None = None,
    health: HealthReport | None = CLEAN_HEALTH,
) -> SmokeJob:
    base = {
        "runner": RUNNER,
        "recipes": RECIPES,
        "execution_target": TARGET,
        "selected": [TRIAL_ID],
        "finished_at": "2026-01-01T00:00:00+00:00",
    }
    merged = {k: v for k, v in {**base, **(manifest or {})}.items() if k not in without}
    return SmokeJob(name, merged, lock or _lock("smoke"), lambda trial_id: trial, lambda r: health)


def _admit(
    *jobs: SmokeJob,
    wanted: dict[str, Any] | None = None,
    kind: str = "ab-test",
    allow: bool = False,
) -> dict[str, Any]:
    found = _wanted() if wanted is None else wanted
    return admit_jobs(kind, found, lambda: list(jobs), allow_unsmoked=allow)


REFUSED = [
    pytest.param({"manifest": {"aborted": True}}, {}, id="aborted"),
    pytest.param({"without": ("finished_at",)}, {}, id="unfinished"),
    pytest.param({"manifest": {"selected": []}}, {}, id="no-trial"),
    pytest.param({}, {"trial": "trial t has an unreadable record"}, id="unreadable-record"),
    pytest.param(
        {},
        {
            "validity": ExecutionValidity(
                checks=(ValidityCheck(name="verifier_infra", passed=False),)
            )
        },
        id="invalid-trial",
    ),
    pytest.param({"health": None}, {}, id="no-health"),
    pytest.param(
        {"health": HealthReport(trial_id=TRIAL_ID, checks=(_hc("H4", "violation"),))},
        {},
        id="health-violation",
    ),
    pytest.param({}, {"agent_override": "oracle"}, id="agent-override"),
    pytest.param(
        {}, {"trial": _smoke_record().model_copy(update={"step_results": ()})}, id="no-step"
    ),
    pytest.param({}, {"digests": (None,)}, id="no-snapshot"),
    pytest.param({}, {"digests": (EMPTY_TREE_DIGEST,)}, id="first-step-changed-nothing"),
    pytest.param({}, {"digests": (DIGEST_A, DIGEST_A)}, id="later-step-changed-nothing"),
    pytest.param({"lock": {**_lock("smoke"), "draft": True}}, {}, id="draft-lock"),
    pytest.param({"manifest": {"runner": None}}, {}, id="incomplete-provenance"),
    pytest.param({"manifest": {"execution_target": {**TARGET, "name": "other"}}}, {}, id="target"),
    pytest.param({"lock": _lock("smoke", _agent(model="gpt-7"))}, {}, id="arm-not-smoked"),
]


@pytest.mark.parametrize(("job", "record"), REFUSED)
def test_a_smoke_that_is_not_clean_or_not_matching_never_clears_a_run(
    job: dict[str, Any], record: dict[str, Any]
) -> None:
    trial = record["trial"] if "trial" in record else _smoke_record(**record)
    smoke = _smoke(trial, **job)

    with pytest.raises(AdmissionError):
        _admit(smoke)
    overridden = _admit(smoke, allow=True)

    assert (overridden["overridden"], overridden["smoke_job"]) == (True, None)
    assert smoke.name in overridden["unmet"]


def test_a_clean_matching_smoke_admits_the_run_and_is_recorded() -> None:
    cleared = _admit(_smoke(_smoke_record(digests=(DIGEST_A, DIGEST_B))))

    assert (cleared["smoke_job"], cleared["smoke_jobs"], cleared["overridden"]) == (
        "exp/smoke",
        ["exp/smoke"],
        False,
    )


def test_a_smoke_run_is_admitted_without_reading_any_earlier_smoke() -> None:
    def unread() -> list[SmokeJob]:
        raise AssertionError("a smoke run needs no earlier smoke")

    assert admit_jobs("smoke", _wanted(), unread, allow_unsmoked=False)["required"] is False


def test_smokes_clear_a_run_together_and_a_smoke_adding_no_arm_is_not_recorded() -> None:
    luna, terra = _agent(), _agent("terra", model="gpt-6-terra")
    wanted = _wanted(luna, terra)
    first = _smoke(_smoke_record("arm-luna"), name="exp/one", lock=_lock("smoke", luna))
    repeat = _smoke(_smoke_record("arm-luna"), name="exp/two", lock=_lock("smoke", luna))
    second = _smoke(_smoke_record("arm-terra"), name="exp/three", lock=_lock("smoke", terra))
    broad = _smoke(_smoke_record("arm-luna"), name="exp/broad", lock=_lock("smoke", luna, terra))

    cleared = _admit(first, repeat, second, wanted=wanted)
    narrower = _admit(broad, wanted=_wanted(luna))

    assert cleared["smoke_jobs"] == ["exp/one", "exp/three"]
    assert narrower["smoke_job"] == "exp/broad"
    with pytest.raises(AdmissionError):
        _admit(first, repeat, wanted=wanted)


@pytest.mark.parametrize(
    "jobs", [(), (_smoke("trial t has no record", name="exp/a"),)], ids=["no-smoke", "unclean"]
)
def test_a_run_with_no_clean_smoke_is_refused_unless_overridden(jobs: tuple[SmokeJob, ...]) -> None:
    with pytest.raises(AdmissionError):
        _admit(*jobs)

    overridden = _admit(*jobs, allow=True)

    assert (overridden["overridden"], overridden["smoke_job"]) == (True, None)
    assert set(overridden["unmet"]) == ({job.name for job in jobs} or {""})


def test_a_run_without_complete_provenance_is_refused_unless_overridden() -> None:
    clean = _smoke(_smoke_record())
    with pytest.raises(AdmissionError):
        admit_jobs("ab-test", None, lambda: [clean], allow_unsmoked=False)

    overridden = admit_jobs("ab-test", None, lambda: [clean], allow_unsmoked=True)

    assert list(overridden["unmet"]) == [""]


def test_arms_with_different_capabilities_are_refused_even_when_overridden() -> None:
    other = _agent("terra", capabilities=OTHER_HARNESS)
    clean = _smoke(_smoke_record())

    with pytest.raises(AdmissionError):
        _admit(clean, wanted=_wanted(_agent(), other), allow=True)
    declared = _admit(clean, wanted=_wanted(_agent(), other, varies=("harness",)), allow=True)

    assert declared["overridden"] is True


def test_a_judge_ledger_without_its_call_list_is_unmeasured(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    del verify["judge_calls"]
    _save(tmp_path, verify)

    assert {c.status for c in _checks(tmp_path).values()} & {"unmeasured"}


def test_health_reports_split_violations_from_unmeasured_and_round_trip_through_the_trial(
    tmp_path: Path,
) -> None:
    checks = tuple(
        HealthCheck(id=f"V{n}", family="V", status=status, detail="d", evidence={})
        for n, status in enumerate(
            cast(tuple[Any, ...], ("pass", "violation", "unmeasured", "violation"))
        )
    )
    report = HealthReport(trial_id="t", checks=checks)

    assert [c.id for c in report.violations] == ["V1", "V3"]
    assert [c.id for c in report.unmeasured] == ["V2"]
    assert load_health(tmp_path) is None
    write_health(tmp_path, report)
    assert load_health(tmp_path) == report


def test_declared_distributions_and_local_modules_excuse_a_missing_import(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "top.py").write_text("")
    (tmp_path / "src" / "inner.py").write_text("")
    (tmp_path / "notes.txt").write_text("")
    local = local_modules(tmp_path)
    log = "".join(
        f"ModuleNotFoundError: No module named '{m}'\n"
        for m in ("pkg", "top", "inner", "notes", "PyYAML", "numpy.linalg")
    )

    assert local == {"pkg", "src", "top", "inner"}
    found = undeclared_modules(log, ["PyYAML>=6", "not a requirement !!"], local)
    assert found == [
        {"module": "notes", "basis": "heuristic"},
        {"module": "numpy", "basis": "heuristic"},
    ]
    assert undeclared_modules(log, [], local)[0]["basis"] == "confirmed"
    assert local_modules(tmp_path / "absent") == set()


def test_setup_scripts_are_written_executable_for_every_step(tmp_path: Path) -> None:
    write_setup_scripts(tmp_path, ["a", "b"])

    for step in ("a", "b"):
        script = tmp_path / "steps" / step / "workdir" / "setup.sh"
        assert script.read_text() == (tmp_path / "steps" / "a" / "workdir" / "setup.sh").read_text()
        assert script.stat().st_mode & 0o111
