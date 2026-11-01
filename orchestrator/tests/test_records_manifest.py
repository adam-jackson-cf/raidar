"""Real trial-directory, sealed-record and health-command seams."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.engine.health.fault_candidates import FAULT_CANDIDATES
from raidar.engine.health.model import HealthReport, load_health, write_health
from raidar.engine.health.reaper import REAP_REPORT
from raidar.engine.health.records_manifest import seal_trial, tamper_findings
from raidar.engine.trial.finish import audit_and_write
from raidar.grading.records import step_record_dir
from tests.health_support import STEP, TRIAL_ID, healthy_trial
from tests.test_trial_health_audit import LATE, _phase, audit, status

pytestmark = pytest.mark.slow


def _trial(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "job"
    trial = job / "trials" / "t1"
    (trial / "evidence").mkdir(parents=True)
    (trial / "evidence" / "a.txt").write_text("a")
    record = job / "trial-records" / "t1.json"
    record.parent.mkdir()
    record.write_text("{}")
    return job, trial, record


def test_retargeting_a_symlinked_directory_is_a_tamper_finding(tmp_path: Path) -> None:
    job, trial, record = _trial(tmp_path)
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    (trial / "linked").symlink_to(tmp_path / "x", target_is_directory=True)
    digest = seal_trial(trial, record, job)
    assert tamper_findings(trial, record, job, digest) == []
    (trial / "linked").unlink()
    (trial / "linked").symlink_to(tmp_path / "y", target_is_directory=True)
    assert tamper_findings(trial, record, job, digest) == ["trials/t1/linked changed"]


def test_an_edited_added_or_removed_file_is_a_tamper_finding(tmp_path: Path) -> None:
    job, trial, record = _trial(tmp_path)
    digest = seal_trial(trial, record, job)
    (trial / "evidence" / "a.txt").write_text("edited")
    (trial / "evidence" / "b.txt").write_text("b")
    assert tamper_findings(trial, record, job, digest) == [
        "trials/t1/evidence/a.txt changed",
        "trials/t1/evidence/b.txt added",
    ]
    (trial / "evidence" / "a.txt").unlink()
    record.write_text('{"forged": true}')
    assert tamper_findings(trial, record, job, digest) == [
        "trial-records/t1.json changed",
        "trials/t1/evidence/a.txt missing",
        "trials/t1/evidence/b.txt added",
    ]
    assert tamper_findings(trial, record, job, None) == ["no records manifest digest in job.json"]


def test_a_healthy_sealed_trial_passes_every_check(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    t.seal()
    checks = audit(t)
    # D1/D2 are readings: a ledger without dependency evidence leaves them unmeasured.
    assert {c.status for k, c in checks.items() if k not in ("D1", "D2")} == {"pass"}, {
        k: (c.status, c.detail) for k, c in checks.items() if c.status != "pass"
    }
    assert {checks["D1"].status, checks["D2"].status} == {"unmeasured"}


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
