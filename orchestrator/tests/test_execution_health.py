"""Run-health audit (mechanics-v1 step 5): each check's pass, violation and unmeasured paths."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.engine.health.audit import audit_trial
from raidar.engine.health.fault_candidates import FAULT_CANDIDATES, append_fault_candidates
from raidar.engine.health.model import HealthCheck, HealthReport, load_health, write_health
from raidar.engine.trial.collect import health_validity
from raidar.engine.trial.finish import HEALTH_ERROR_FILE, audit_and_write
from raidar.engine.trial.models import CheckpointState, ValidityCheck
from raidar.engine.trial.reaper import REAP_REPORT
from raidar.grading.records import step_record_dir
from tests.execution_support import StepOutcome
from tests.health_support import STEP, TRIAL_ID, HealthyTrial, healthy_trial

LATE = "2026-09-30T14:00:00+00:00"  # two hours into a trial whose phases are seconds long


def audit(t: HealthyTrial, record: Any = None) -> dict[str, HealthCheck]:
    report = audit_trial(t.job_dir, t.trial_dir, record or t.record, t.job())
    return {check.id: check for check in report.checks}


def status(t: HealthyTrial, check_id: str, record: Any = None) -> str:
    return audit(t, record)[check_id].status


def with_step(t: HealthyTrial, **changes: Any) -> Any:
    first = t.record.step_results[0].model_copy(update=changes)
    return t.record.model_copy(update={"step_results": (first, *t.record.step_results[1:])})


def drop_job_keys(t: HealthyTrial, *keys: str) -> None:
    t.job_file.write_text(json.dumps({k: v for k, v in t.job().items() if k not in keys}))


def test_a_healthy_sealed_trial_passes_every_check(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    checks = audit(t)
    assert {c.status for c in checks.values()} == {"pass"}, {
        k: (c.status, c.detail) for k, c in checks.items() if c.status != "pass"
    }
    assert [c.id for c in checks.values()] == [
        "V1", "V2", "V3", "H1", "H3", "H4", "H5", "H6", "H-target", "L1", "L2", "P2", "P3",
        "T1", "T2", "T3",
    ]  # fmt: skip
    assert {c.family for c in checks.values()} == {"V", "H", "L", "P", "T"}


def test_v1_a_timed_out_evaluator_is_never_a_pass(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path, [StepOutcome(STEP, infra="sandbox_timeout")])
    assert status(t, "V1") == "pass"  # classified as infra, not counted
    counted = with_step(t, checkpoint_state=CheckpointState.SOLVED)
    found = audit(t, counted)["V1"]
    assert found.status == "violation"
    assert found.evidence["steps"] == [STEP]


def test_v_checks_without_a_verifier_record_are_unmeasured(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path, [StepOutcome(STEP, passed=None)])
    assert {status(t, i) for i in ("V1", "V2", "V3")} == {"unmeasured"}


def test_v2_case_counts_must_agree_between_ledger_and_record(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    assert status(t, "V2") == "pass"
    found = audit(t, with_step(t, strict_pass_rate=0.1))["V2"]
    assert found.status == "violation"
    assert "strict_pass_rate" in found.detail


def test_v3_the_evaluated_tree_must_equal_the_stored_snapshot(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    app = t.trial_dir / "steps" / STEP / "artifacts" / "app"
    (app / "main.py").write_text("print('edited after grading')\n")
    assert status(t, "V3") == "violation"
    shutil.rmtree(app)
    assert status(t, "V3") == "unmeasured"


def test_h1_every_expected_record_file_is_present(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    (step_record_dir(t.trial_dir, STEP) / "pre-agent-leak.json").unlink()
    found = audit(t)["H1"]
    assert found.status == "violation"
    assert found.evidence["missing"] == [f"{STEP}/pre-agent-leak.json"]


def test_h3_effective_limits_must_be_recorded(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    limits = {k: v for k, v in t.job()["effective_limits"].items() if k != "verifier_s"}
    t.set_job(effective_limits=limits)
    assert status(t, "H3") == "violation"
    drop_job_keys(t, "effective_limits")
    assert status(t, "H3") == "unmeasured"
    assert {status(t, i) for i in ("H4", "H5", "H6")} == {"unmeasured"}


@pytest.mark.parametrize(
    ("check_id", "edit"),
    [
        ("H4", lambda r: r["step_results"][0]["agent_execution"].update(finished_at=LATE)),
        ("H5", lambda r: r["agent_setup"].update(finished_at=LATE)),
        ("H6", lambda r: r["step_results"][0]["verifier"].update(finished_at=LATE)),
    ],
)
def test_h4_to_h6_phase_wall_times_stay_within_the_effective_limits(
    tmp_path: Path, check_id: str, edit: Callable[[dict[str, Any]], None]
) -> None:
    t = healthy_trial(tmp_path)
    assert status(t, check_id) == "pass"
    t.patch_result(edit)
    assert status(t, check_id) == "violation"


def test_h4_an_unrecorded_limit_is_unmeasured_not_a_pass(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.set_job(effective_limits={**t.job()["effective_limits"], "agent_s": {"baseline": None}})
    assert audit(t)["H4"].status == "unmeasured"
    assert "default" in audit(t)["H4"].detail


def test_h_target_mirrors_the_guards_target_reachable(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    lost = ValidityCheck(name="target_reachable", passed=False, detail="daemon down")
    record = t.record.model_copy(
        update={
            "validity": t.record.validity.model_copy(
                update={"checks": (*t.record.validity.checks, lost)}
            )
        }
    )
    assert audit(t, record)["H-target"].status == "violation"
    drop_job_keys(t, "execution_target")
    assert status(t, "H-target") == "unmeasured"


def test_l1_and_l2_record_liveness_per_agent_phase(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    assert (status(t, "L1"), status(t, "L2")) == ("pass", "pass")
    (t.trial_dir / "raidar-liveness.json").unlink()
    assert (status(t, "L1"), status(t, "L2")) == ("unmeasured", "unmeasured")
    t.write("raidar-liveness.json", {"phases": []})
    assert status(t, "L1") == "unmeasured"
    assert status(t, "L2") == "unmeasured"


def _phase(decision: str, **extra: Any) -> dict[str, Any]:
    base = {"step": STEP, "decision": decision, "sample_count": 5, "unmeasured_samples": 0}
    return {**base, "reason": "no output, agent CPU or network for 60s", **extra}


KILL = {"killed": ["42 1000 S node codex", "43 1000 S sleep 9999"], "residual": [], "error": None}


def test_l2_idle_observed_and_idle_killed_are_passes_with_findings(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.write("raidar-liveness.json", {"phases": [_phase("idle_observed", first_idle_at_s=61.0)]})
    observed = audit(t)["L2"]
    assert observed.status == "pass"
    assert [f["decision"] for f in observed.evidence["findings"]] == ["idle_observed"]
    killed_phase = _phase("idle_killed", first_idle_at_s=61.0, idle_for_s=600.0, kill=KILL)
    t.write("raidar-liveness.json", {"phases": [killed_phase]})
    killed = audit(t)["L2"]
    assert killed.status == "pass"
    (finding,) = killed.evidence["findings"]
    assert (finding["decision"], finding["idle_for_s"]) == ("idle_killed", 600.0)
    assert finding["killed_pids"] == ["42", "43"]
    assert "600.0 s" in killed.detail
    t.write("raidar-liveness.json", {"phases": [_phase("kill_failed", kill=KILL)]})
    assert status(t, "L2") == "violation"
    blind = _phase("active", sample_count=3, unmeasured_samples=3)
    t.write("raidar-liveness.json", {"phases": [blind]})
    assert status(t, "L2") == "unmeasured"


def test_p2_and_p3_carry_concurrency_and_comparison_warnings(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    p2 = audit(t)["P2"]
    assert (p2.evidence["configured"], p2.evidence["effective"]) == (1, 1)
    t.set_job(runner={**t.job()["runner"], "dirty": True}, admission={"overridden": True})
    p3 = audit(t)["P3"]
    assert p3.status == "pass"
    assert len(p3.evidence["warnings"]) == 2
    drop_job_keys(t, "concurrency", "execution_target", "runner")
    assert (status(t, "P2"), status(t, "P3")) == ("unmeasured", "unmeasured")


def test_t1_a_changed_tool_tree_is_a_violation_and_a_missing_hash_unmeasured(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    point = {"present": True, "entries": 3, "measured": True, "step": STEP}
    tampered = [
        {**point, "point": "baseline", "sha256": "a" * 64},
        {**point, "point": "after_agent", "sha256": "b" * 64},
    ]
    t.write("raidar-tool-hashes.json", {"points": tampered})
    found = audit(t)["T1"]
    assert found.status == "violation"
    assert found.evidence["steps"] == [STEP]
    unmeasured = {"point": "after_agent", "step": STEP, "measured": False, "unmeasured": "boom"}
    t.write("raidar-tool-hashes.json", {"points": [tampered[0], unmeasured]})
    assert status(t, "T1") == "unmeasured"
    t.write("raidar-tool-hashes.json", {"points": [unmeasured]})
    assert status(t, "T1") == "unmeasured"
    (t.trial_dir / "raidar-tool-hashes.json").unlink()
    assert status(t, "T1") == "unmeasured"


def test_t1_no_tool_tree_that_stays_absent_passes(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    absent = {"present": False, "sha256": None, "entries": 0, "measured": True, "step": STEP}
    points = [{**absent, "point": "baseline"}, {**absent, "point": "after_agent"}]
    t.write("raidar-tool-hashes.json", {"points": points})
    assert audit(t)["T1"].status == "pass"
    points[1] = {**points[1], "present": True, "sha256": "c" * 64}
    t.write("raidar-tool-hashes.json", {"points": points})
    assert status(t, "T1") == "violation"


def test_t2_the_sealed_records_manifest_must_verify(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    assert audit(t)["T2"].status == "unmeasured"  # sealed after the audit, in a live job
    t.seal()
    assert status(t, "T2") == "pass"
    (t.trial_dir / "steps" / STEP / "agent").mkdir(parents=True, exist_ok=True)
    (t.trial_dir / "steps" / STEP / "agent" / "forged.txt").write_text("x")
    found = audit(t)["T2"]
    assert found.status == "violation"
    assert "forged.txt added" in found.detail


def test_t2_a_health_json_rewrite_does_not_break_the_seal(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    write_health(t.trial_dir, HealthReport(trial_id=TRIAL_ID, checks=()))
    assert status(t, "T2") == "pass"


def test_t3_a_reaper_residual_is_a_violation(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    reap = step_record_dir(t.trial_dir, STEP) / REAP_REPORT
    reap.write_text(json.dumps({"user": "agent", "killed": [], "residual": ["5 sleep"]}))
    assert audit(t)["T3"].status == "violation"
    reap.unlink()
    assert status(t, "T3") == "unmeasured"


def test_fault_candidates_record_violations_and_idle_findings_once(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.write("raidar-liveness.json", {"phases": [_phase("idle_observed", first_idle_at_s=61.0)]})
    t.patch_result(lambda r: r["agent_setup"].update(finished_at=LATE))
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    written = append_fault_candidates(t.job_dir, report)
    lines = [json.loads(x) for x in (t.job_dir / FAULT_CANDIDATES).read_text().splitlines()]
    assert lines == written
    by_id = {line["check_id"]: line for line in lines}
    assert set(by_id) == {"H5", "L2"}
    assert by_id["L2"]["status"] == "idle_observed"
    assert by_id["L2"]["step"] == STEP
    assert by_id["H5"]["status"] == "violation"
    assert {line["state"] for line in lines} == {"provisional"}
    assert all(line["trial"] == TRIAL_ID for line in lines)
    assert append_fault_candidates(t.job_dir, report) == []  # idempotent on re-audit
    assert len((t.job_dir / FAULT_CANDIDATES).read_text().splitlines()) == 2


def test_an_idle_kill_is_a_fault_candidate_with_its_reason_and_pids(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    killed = _phase("idle_killed", first_idle_at_s=61.0, idle_for_s=600.0, kill=KILL)
    t.write("raidar-liveness.json", {"phases": [killed]})
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    (line,) = append_fault_candidates(t.job_dir, report)
    assert (line["check_id"], line["status"], line["step"]) == ("L2", "idle_killed", STEP)
    assert "killed pids ['42', '43']" in line["detail"]
    assert append_fault_candidates(t.job_dir, report) == []


def _report(*checks: tuple[str, str]) -> HealthReport:
    items = [
        HealthCheck(id=i, family=cast(Any, i[0]), status=cast(Any, s), detail=f"{i} {s}")
        for i, s in checks
    ]
    return HealthReport(trial_id="t", checks=tuple(items))


def test_violations_map_to_validity_checks_and_nothing_else_invalidates() -> None:
    clean = health_validity(
        _report(("L2", "pass"), ("P3", "pass"), ("T1", "unmeasured"), ("H-target", "violation"))
    )
    assert [c.name for c in clean] == [
        "health_verdicts", "health_budgets", "health_tamper",
    ]  # fmt: skip
    assert all(c.passed for c in clean)  # H-target is the guard's own target_reachable
    failed = {
        c.name: c
        for c in health_validity(
            _report(
                ("V2", "violation"), ("H4", "violation"), ("T3", "violation"), ("L2", "violation")
            )
        )
    }
    assert not any(c.passed for c in failed.values())
    assert "V2: V2 violation" in failed["health_verdicts"].detail
    assert "H4" in failed["health_budgets"].detail
    assert "T3" in failed["health_tamper"].detail


def test_the_trial_run_writes_health_json_and_maps_its_violations(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.patch_result(lambda r: r["agent_setup"].update(finished_at=LATE))
    checks = {c.name: c for c in audit_and_write(t.job_dir, t.trial_dir, t.record)}
    assert checks["health_budgets"].passed is False
    assert "H5" in checks["health_budgets"].detail
    assert checks["health_verdicts"].passed is True
    stored = load_health(t.trial_dir)
    assert stored is not None
    assert [c.id for c in stored.violations] == ["H5"]
    assert (t.job_dir / FAULT_CANDIDATES).is_file()


def test_an_audit_that_raises_invalidates_the_trial_and_keeps_the_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = healthy_trial(tmp_path)

    def boom(*args: Any) -> HealthReport:
        raise RuntimeError("audit bug")

    monkeypatch.setattr("raidar.engine.trial.finish.audit_trial", boom)
    (check,) = audit_and_write(t.job_dir, t.trial_dir, t.record)
    assert (check.name, check.passed) == ("health_audit", False)
    assert "audit bug" in (t.trial_dir / HEALTH_ERROR_FILE).read_text()


def test_raidar_health_recomputes_and_exits_nonzero_on_a_violation(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    runner = CliRunner()
    ok = runner.invoke(main, ["health", str(t.job_dir)])
    assert ok.exit_code == 0, ok.output
    assert "0 violation(s)" in ok.output
    stored = load_health(t.trial_dir)
    assert stored is not None
    assert next(c for c in stored.checks if c.id == "T2").status == "pass"
    (t.trial_dir / "raidar-liveness.json").write_text(
        json.dumps({"phases": [_phase("kill_failed")]})
    )
    bad = runner.invoke(main, ["health", str(t.job_dir)])
    assert bad.exit_code == 1
    assert "T2" in bad.output  # the liveness file changed after the seal: tamper, too
    lines = [json.loads(x) for x in (t.job_dir / FAULT_CANDIDATES).read_text().splitlines()]
    assert {line["check_id"] for line in lines} == {"L2", "T2"}
    empty = runner.invoke(main, ["health", str(tmp_path)])
    assert empty.exit_code != 0
