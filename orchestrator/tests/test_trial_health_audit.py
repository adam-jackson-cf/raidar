"""Health decisions over in-memory evidence; real directory seams live in records-manifest tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from raidar.contract.trial import (
    CheckpointState,
    ExecutionValidity,
    StepResult,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.health import audit as checks
from raidar.engine.health import signals
from raidar.engine.health.base import Audit
from raidar.engine.health.fault_candidates import FAULT_CANDIDATES, append_fault_candidates
from raidar.engine.health.model import HealthCheck, HealthReport, load_health
from raidar.engine.trial.collect import health_validity
from raidar.engine.trial.finish import HEALTH_ERROR_FILE, audit_and_write
from tests.execution_support import StepOutcome, _record
from tests.health_support import LIMITS, STEP, TRIAL_ID, _context

LATE = "2026-09-30T14:00:00+00:00"
KILL = {"killed": ["42 1000 S node codex", "43 1000 S sleep 9999"], "residual": [], "error": None}


def _phase(decision: str, **extra: Any) -> dict[str, Any]:
    base = {"step": STEP, "decision": decision, "sample_count": 5, "unmeasured_samples": 0}
    return {**base, "reason": "no output, agent CPU or network for 60s", **extra}


@pytest.fixture
def evidence() -> Audit:
    """An in-memory audit: checks consume real record/ledger models and parsed result data."""
    ledger = _record(STEP, StepOutcome(STEP), Path("/trial"))
    ctx = _context((STEP,))
    step = StepResult(
        trial_id=TRIAL_ID,
        arm="baseline",
        attempt=0,
        problem=ctx.trial.problem,
        index=0,
        step=STEP,
        checkpoint=STEP,
        checkpoint_state=CheckpointState.SOLVED,
        strict_pass_rate=ledger.grade.pass_rate(),
        rewards=ledger.rewards,
    )
    record = TrialRecord(
        experiment="exp",
        job_id="j",
        trial=ctx.trial,
        trial_dir=f"trials/{TRIAL_ID}",
        progression=ctx.rule,
        step_results=(step,),
        reached=(True,),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="trial_completed", passed=True),)),
    )
    span = {"started_at": "2026-09-30T12:00:00+00:00", "finished_at": "2026-09-30T12:00:01+00:00"}
    result = {
        "agent_setup": dict(span),
        "step_results": [
            {"step_name": STEP, "agent_execution": dict(span), "verifier": dict(span)}
        ],
    }
    return Audit(
        Path("/job"),
        Path(f"/job/trials/{TRIAL_ID}"),
        record,
        {
            "runner": {"commit": "abc", "dirty": False},
            "effective_limits": deepcopy(LIMITS),
            "concurrency": 1,
            "execution_target": {"name": "local", "docker": {"ncpu": 4}},
        },
        result=result,
        ledgers={STEP: ledger},
    )


def test_v1_a_timed_out_evaluator_is_never_a_pass(evidence: Audit) -> None:
    ledger = _record(STEP, StepOutcome(STEP, infra="sandbox_timeout"), Path("/trial"))
    evidence._ledgers = {**evidence._ledgers, STEP: ledger}
    evidence.attempted = (
        evidence.attempted[0].model_copy(
            update={
                "checkpoint_state": CheckpointState.INFRA_ERROR,
                "rewards": None,
            }
        ),
    )
    assert checks.check_v1(evidence).status == "pass"
    evidence.attempted = (
        evidence.attempted[0].model_copy(
            update={
                "checkpoint_state": CheckpointState.SOLVED,
            }
        ),
    )
    found = checks.check_v1(evidence)
    assert found.status == "violation"
    assert found.evidence["steps"] == [STEP]


def test_v_checks_without_a_verifier_record_are_unmeasured(evidence: Audit) -> None:
    evidence._ledgers = {**evidence._ledgers, STEP: None}
    assert {
        check(evidence).status for check in (checks.check_v1, checks.check_v2, checks.check_v3)
    } == {"unmeasured"}


def test_v2_case_counts_must_agree_between_ledger_and_record(evidence: Audit) -> None:
    assert checks.check_v2(evidence).status == "pass"
    evidence.attempted = (evidence.attempted[0].model_copy(update={"strict_pass_rate": 0.1}),)
    found = checks.check_v2(evidence)
    assert found.status == "violation"
    assert "strict_pass_rate" in found.evidence["mismatches"][STEP]


def test_h3_effective_limits_must_be_recorded(evidence: Audit) -> None:
    job = cast(dict[str, Any], evidence.job)
    del job["effective_limits"]["verifier_s"]
    assert checks.check_h3(evidence).status == "violation"
    del job["effective_limits"]
    assert checks.check_h3(evidence).status == "unmeasured"
    assert {
        check(evidence).status for check in (checks.check_h4, checks.check_h5, checks.check_h6)
    } == {"unmeasured"}


@pytest.mark.parametrize(
    ("check", "phase"),
    [
        (checks.check_h4, "agent_execution"),
        (checks.check_h5, "agent_setup"),
        (checks.check_h6, "verifier"),
    ],
)
def test_phase_wall_times_stay_within_the_effective_limits(
    evidence: Audit,
    check: Callable[[Audit], HealthCheck],
    phase: str,
) -> None:
    assert check(evidence).status == "pass"
    result = cast(dict[str, Any], evidence.result)
    span = result[phase] if phase == "agent_setup" else evidence.items[STEP][phase]
    span["finished_at"] = LATE
    assert check(evidence).status == "violation"


def test_h4_an_unrecorded_limit_is_unmeasured_not_a_pass(evidence: Audit) -> None:
    cast(dict[str, Any], evidence.job)["effective_limits"]["agent_s"]["baseline"] = None
    assert checks.check_h4(evidence).status == "unmeasured"


def test_h_target_requires_reachability_evidence(evidence: Audit) -> None:
    lost = ValidityCheck(name="target_reachable", passed=False, detail="daemon down")
    evidence.record = evidence.record.model_copy(
        update={"validity": ExecutionValidity(checks=(lost,))}
    )
    assert checks.check_h_target(evidence).status == "violation"
    evidence.record = evidence.record.model_copy(update={"validity": ExecutionValidity(checks=())})
    cast(dict[str, Any], evidence.job).pop("execution_target")
    assert checks.check_h_target(evidence).status == "unmeasured"


def test_l1_and_l2_record_liveness_per_agent_phase(
    evidence: Audit, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload: dict[str, Any] | None = {"phases": [_phase("active")]}
    monkeypatch.setattr(signals, "_load_json", lambda path: payload)
    assert (signals.check_l1(evidence).status, signals.check_l2(evidence).status) == (
        "pass",
        "pass",
    )
    payload = None
    assert (signals.check_l1(evidence).status, signals.check_l2(evidence).status) == (
        "unmeasured",
        "unmeasured",
    )
    payload = {"phases": []}
    assert (signals.check_l1(evidence).status, signals.check_l2(evidence).status) == (
        "unmeasured",
        "unmeasured",
    )


def test_l2_idle_findings_distinguish_observation_kills_and_blindness(
    evidence: Audit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phases = [_phase("idle_observed", first_idle_at_s=61.0)]
    monkeypatch.setattr(signals, "_load_json", lambda path: {"phases": phases})
    observed = signals.check_l2(evidence)
    assert observed.status == "pass"
    assert [f["decision"] for f in observed.evidence["findings"]] == ["idle_observed"]
    phases[:] = [_phase("idle_killed", first_idle_at_s=61.0, idle_for_s=600.0, kill=KILL)]
    killed = signals.check_l2(evidence)
    assert killed.status == "pass"
    (finding,) = killed.evidence["findings"]
    assert (finding["decision"], finding["idle_for_s"], finding["killed_pids"]) == (
        "idle_killed",
        600.0,
        ["42", "43"],
    )
    phases[:] = [_phase("kill_failed", kill=KILL)]
    assert signals.check_l2(evidence).status == "violation"
    phases[:] = [_phase("active", sample_count=3, unmeasured_samples=3)]
    assert signals.check_l2(evidence).status == "unmeasured"


def test_comparison_warnings_do_not_invalidate_and_missing_provenance_is_unmeasured(
    evidence: Audit,
) -> None:
    assert signals.check_p3(evidence).status == "pass"
    job = cast(dict[str, Any], evidence.job)
    job["runner"]["dirty"] = True
    job["admission"] = {"overridden": True}
    found = signals.check_p3(evidence)
    assert found.status == "pass"
    assert len(found.evidence["warnings"]) == 2
    del job["runner"]
    assert signals.check_p3(evidence).status == "unmeasured"


def test_tool_integrity_requires_a_measured_baseline_and_complete_unchanged_samples() -> None:
    point = {"present": True, "entries": 3, "measured": True, "step": STEP}
    baseline = {**point, "point": "baseline", "sha256": "a" * 64}
    later = {**point, "point": "after_agent", "sha256": "b" * 64}
    status_, _, detail = signals.tool_status([baseline, later])
    assert status_ == "violation"
    assert detail["steps"] == [STEP]
    blind = {"point": "after_agent", "step": STEP, "measured": False, "unmeasured": "boom"}
    assert signals.tool_status([baseline, blind])[0] == "unmeasured"
    assert signals.tool_status([blind])[0] == "unmeasured"
    absent = {"present": False, "sha256": None, "entries": 0, "measured": True, "step": STEP}
    points = [{**absent, "point": "baseline"}, {**absent, "point": "after_agent"}]
    assert signals.tool_status(points)[0] == "pass"
    points[1] = {**points[1], "present": True, "sha256": "c" * 64}
    assert signals.tool_status(points)[0] == "violation"


def _report(*checks_: tuple[str, str]) -> HealthReport:
    items = [
        HealthCheck(id=i, family=cast(Any, i[0]), status=cast(Any, s), detail=f"{i} {s}")
        for i, s in checks_
    ]
    return HealthReport(trial_id=TRIAL_ID, checks=tuple(items))


def test_violations_map_to_validity_checks_and_nothing_else_invalidates() -> None:
    clean = health_validity(
        _report(
            ("L2", "pass"),
            ("P3", "pass"),
            ("T1", "unmeasured"),
            ("J2", "unmeasured"),
            ("D1", "violation"),
            ("H-target", "violation"),
        )
    )
    assert all(c.passed for c in clean)
    failed = {
        c.name: c
        for c in health_validity(
            _report(
                ("V2", "violation"),
                ("H4", "violation"),
                ("T3", "violation"),
                ("L2", "violation"),
                ("P8", "violation"),
                ("J2", "violation"),
                ("W2", "violation"),
            )
        )
    }
    assert not any(c.passed for c in failed.values())
    assert "V2: V2 violation" in failed["health_verdicts"].detail
    assert "H4" in failed["health_budgets"].detail
    assert "T3" in failed["health_tamper"].detail
    assert "J2" in failed["health_judge"].detail
    assert "W2" in failed["health_evidence"].detail


@pytest.mark.parametrize("decision", ["idle_observed", "idle_killed"])
def test_fault_candidates_retain_idle_findings_once(
    tmp_path: Path,
    evidence: Audit,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    phases = [_phase(decision, first_idle_at_s=61.0, idle_for_s=600.0, kill=KILL)]
    monkeypatch.setattr(signals, "_load_json", lambda path: {"phases": phases})
    report = HealthReport(
        trial_id=TRIAL_ID, checks=(signals.check_l2(evidence), *_report(("H5", "violation")).checks)
    )
    written = append_fault_candidates(tmp_path, report)
    lines = [json.loads(x) for x in (tmp_path / FAULT_CANDIDATES).read_text().splitlines()]
    assert lines == written
    by_id = {line["check_id"]: line for line in lines}
    assert set(by_id) == {"H5", "L2"}
    assert by_id["L2"]["status"] == decision
    assert by_id["L2"]["step"] == STEP
    assert by_id["H5"]["status"] == "violation"
    assert {line["state"] for line in lines} == {"provisional"}
    assert all(line["trial"] == TRIAL_ID for line in lines)
    if decision == "idle_killed":
        assert "killed pids ['42', '43']" in by_id["L2"]["detail"]
    assert append_fault_candidates(tmp_path, report) == []
    assert len((tmp_path / FAULT_CANDIDATES).read_text().splitlines()) == 2


@pytest.mark.parametrize("check_id", ["W3", "J2"])
def test_non_liveness_findings_are_retained_as_faults(tmp_path: Path, check_id: str) -> None:
    check = HealthCheck(
        id=check_id,
        family=cast(Any, check_id[0]),
        status="violation",
        detail="the recorded inputs disagree",
        evidence={"findings": ["input mismatch"]},
    )
    (fault,) = append_fault_candidates(tmp_path, HealthReport(trial_id=TRIAL_ID, checks=(check,)))
    assert fault["check_id"] == check_id
    assert fault["status"] == "violation"
    assert fault["evidence"] == {"findings": ["input mismatch"]}


@pytest.mark.parametrize("broken", [False, True], ids=["violations", "audit-error"])
def test_finalization_preserves_health_failures_and_diagnostic_evidence(
    tmp_path: Path,
    evidence: Audit,
    monkeypatch: pytest.MonkeyPatch,
    broken: bool,
) -> None:
    def calculate(*args: Any) -> HealthReport:
        if broken:
            raise RuntimeError("audit bug")
        return _report(("H5", "violation"), ("V2", "pass"))

    monkeypatch.setattr("raidar.engine.trial.finish.audit_trial", calculate)
    found = {c.name: c for c in audit_and_write(tmp_path, tmp_path, evidence.record)}
    if broken:
        assert found["health_audit"].passed is False
        assert "audit bug" in (tmp_path / HEALTH_ERROR_FILE).read_text()
    else:
        assert found["health_budgets"].passed is False
        assert found["health_verdicts"].passed is True
        stored = load_health(tmp_path)
        assert stored is not None
        assert [c.id for c in stored.violations] == ["H5"]
        assert (tmp_path / FAULT_CANDIDATES).is_file()
