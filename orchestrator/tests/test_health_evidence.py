"""Stored-trial seams of run health: the lab-owned evidence rows (W) and the isolation check (P8)
read real record files (agent logs, stored snapshots, rollouts, the lab's ``config.json``) of a
finished trial through ``audit_trial``. The decisions themselves are proved in memory in
``test_health_judge.py`` and ``test_health_isolation.py``; these scenarios only prove that the
readers find, hash and parse what a trial left on disk."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from raidar.contract.harbor_result import RESULT_FILE
from raidar.contract.job_layout import TRIAL_RECORDS_DIR
from raidar.engine.health.admission import admit, smoke_problem
from raidar.engine.health.audit import audit_trial
from raidar.engine.health.model import HealthCheck, write_health
from raidar.engine.health.records_manifest import RECORDS_MANIFEST, seal_trial, tamper_findings
from raidar.engine.health.signals import check_t2
from raidar.engine.trial.collect import health_validity
from raidar.grading.records import RECORD_FILE, step_record_dir, write_step_record
from raidar.subject.agent.config import Harness
from tests.execution_support import StepOutcome
from tests.health_support import (
    CRITERIA_FILE,
    STEP,
    TRIAL_ID,
    HealthyTrial,
    healthy_trial,
    memory_job,
    memory_ledger,
    memory_result,
    memory_step,
    memory_trial,
    package_records,
    write_agent_session,
    write_package_config,
)
from tests.native_support import session_audit
from tests.test_health_judge import (
    CLEAN_HEALTH,
    _audit,
    _lock,
    _smoke,
    _smoke_record,
    _trial_dir,
    _wanted,
)

pytestmark = pytest.mark.slow

SECOND = "checkpoint_2"
WORKFLOW = "workflow/workflow.json"
HOOK_LOG = "gate/hook-runs.jsonl"


def _agent(t: HealthyTrial, step: str = STEP) -> Path:
    return t.trial_dir / "steps" / step / "agent"


def _package_trial(tmp_path: Path) -> HealthyTrial:
    """A trial whose lab config locks every primitive and whose records are all on disk."""
    t = healthy_trial(tmp_path)
    write_package_config(t.trial_dir)
    agent = _agent(t)
    workflow = json.loads((agent / WORKFLOW).read_text())
    workflow |= package_records(agent)
    (agent / WORKFLOW).write_text(json.dumps(workflow))
    return t


def _w(t: HealthyTrial) -> dict[str, HealthCheck]:
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return {c.id: c for c in report.checks if c.id.startswith("W")}


def _invalid(t: HealthyTrial) -> bool:
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return not all(c.passed for c in health_validity(report))


def _rewrite(agent: Path, change: Callable[[dict[str, Any]], None]) -> None:
    workflow = json.loads((agent / WORKFLOW).read_text())
    change(workflow)
    (agent / WORKFLOW).write_text(json.dumps(workflow))


def test_a_complete_package_trial_holds_every_row_on_disk(tmp_path: Path) -> None:
    t = _package_trial(tmp_path)

    checks = _w(t)

    assert {c.status for c in checks.values()} == {"pass"}
    assert not any("not applicable" in c.detail for c in checks.values())
    assert not _invalid(t)


@pytest.mark.parametrize(
    ("row", "relative"),
    [
        ("W3", "gate/gate-1.json"),
        ("W8", CRITERIA_FILE),
        ("W10", "gate/baseline.json"),
        ("W10", "workflow/judge/call-0.response.json"),
        ("W10", "workflow/build-1/last-message.txt"),
    ],
)
def test_a_deleted_stored_record_file_invalidates_the_trial(
    tmp_path: Path, row: str, relative: str
) -> None:
    t = _package_trial(tmp_path)
    (_agent(t) / relative).unlink()

    checks = _w(t)

    assert checks[row].status == "violation", checks[row].detail
    assert _invalid(t)


def test_a_rewritten_output_file_is_not_at_its_recorded_digest(tmp_path: Path) -> None:
    t = _package_trial(tmp_path)
    (_agent(t) / CRITERIA_FILE).write_text("rewritten by the agent\n")

    assert _w(t)["W8"].status == "violation"


def test_a_deleted_session_rollout_invalidates_the_trial(tmp_path: Path) -> None:
    t = _package_trial(tmp_path)
    next(_agent(t).rglob("rollout-*.jsonl")).unlink()

    assert _w(t)["W2"].status == "violation"
    assert _invalid(t)


def test_a_missing_or_malformed_hook_log_is_a_reading_that_never_invalidates(
    tmp_path: Path,
) -> None:
    t = _package_trial(tmp_path)
    (_agent(t) / HOOK_LOG).write_text("{not json\n")
    assert _w(t)["W5"].status == "unmeasured"
    (_agent(t) / HOOK_LOG).unlink()
    assert _w(t)["W5"].status == "unmeasured"
    assert not _invalid(t)


def test_deleting_every_record_is_invalid_because_the_lock_still_owes_them(
    tmp_path: Path,
) -> None:
    t = _package_trial(tmp_path)
    for path in sorted(_agent(t).rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()

    violated = {i for i, c in _w(t).items() if c.status == "violation"}

    assert violated == {"W1", "W3", "W4", "W6", "W8", "W9", "W10"}
    assert _invalid(t)


def test_a_real_agent_run_without_a_readable_config_is_invalid_not_a_fallback(
    tmp_path: Path,
) -> None:
    t = _package_trial(tmp_path)
    (t.trial_dir / "config.json").unlink()

    checks = _w(t)

    assert checks["W1"].status == "violation"
    assert "applicability unknown" in checks["W1"].detail
    assert checks["W5"].status == "unmeasured"


def test_the_result_json_config_stands_in_for_a_missing_config_json(tmp_path: Path) -> None:
    t = _package_trial(tmp_path)
    config = json.loads((t.trial_dir / "config.json").read_text())
    (t.trial_dir / "config.json").unlink()
    result_path = t.trial_dir / "result.json"
    result = json.loads(result_path.read_text())
    result_path.write_text(json.dumps(result | {"config": config}))

    assert {c.status for c in _w(t).values()} == {"pass"}


def test_a_no_native_override_owes_no_workflow_evidence_though_its_record_is_gone(
    tmp_path: Path,
) -> None:
    t = healthy_trial(tmp_path)
    (t.trial_dir / "config.json").write_text(json.dumps({"agent": {"name": "oracle"}}))
    (_agent(t) / WORKFLOW).unlink()

    checks = _w(t)

    assert {c.status for c in checks.values()} == {"pass"}
    assert all("not applicable" in c.detail for c in checks.values())


def test_a_package_free_trial_still_owes_its_workflow_record(tmp_path: Path) -> None:
    t = healthy_trial(tmp_path)
    assert {c.status for c in _w(t).values()} == {"pass"}
    (_agent(t) / WORKFLOW).unlink()

    assert _w(t)["W1"].status == "violation"
    assert _invalid(t)


# ---- P8: the isolation check over stored snapshots, workflow records and native sessions ------

SKILLS = "<skills_instructions>\n### Skill roots\n{roots}\n</skills_instructions>"
AGENTS = "# AGENTS.md instructions for /app\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"


def _roots(*paths: str) -> str:
    return SKILLS.format(roots="\n".join(f"- `r{i}` = `{p}`" for i, p in enumerate(paths)))


def _p8(t: HealthyTrial) -> HealthCheck:
    report = audit_trial(t.job_dir, t.trial_dir, t.record, t.job())
    return next(c for c in report.checks if c.id == "P8")


def _session(tmp_path: Path, *rows: str) -> HealthyTrial:
    t = healthy_trial(tmp_path)
    write_agent_session(t.trial_dir, STEP, list(rows))
    return t


def _second_step(tmp_path: Path, *rows: str) -> HealthyTrial:
    """A two-step trial whose second step ran ``rows``; step one's snapshot is its start state."""
    t = healthy_trial(tmp_path, [StepOutcome(STEP), StepOutcome(SECOND)])
    write_agent_session(t.trial_dir, SECOND, list(rows))
    return t


def _snapshot(t: HealthyTrial, step: str) -> Path:
    return t.trial_dir / "steps" / step / "artifacts" / "app"


def test_a_plain_prompt_with_workspace_skills_passes_from_a_real_rollout(tmp_path: Path) -> None:
    check = _p8(_session(tmp_path, _roots("/app/.agents/skills"), "solve the task"))

    assert check.status == "pass", check.detail


def test_agents_lines_are_checked_against_the_stored_snapshot_before_the_turn(
    tmp_path: Path,
) -> None:
    t = _second_step(tmp_path, AGENTS.format(body="be terse\nlate rule"))
    (_snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    (_snapshot(t, SECOND) / "AGENTS.md").write_text("be terse\nlate rule\n")

    found = _p8(t)

    assert found.status == "violation"
    assert "late rule" in found.detail
    (_snapshot(t, STEP) / "AGENTS.override.md").write_text("late rule\n")
    assert _p8(t).status == "pass"


def test_agents_text_without_a_stored_snapshot_is_unmeasured(tmp_path: Path) -> None:
    t = _second_step(tmp_path, AGENTS.format(body="be terse"))
    shutil.rmtree(t.trial_dir / "steps" / STEP / "artifacts")

    check = _p8(t)

    assert check.status == "unmeasured"
    assert "before the first step" in check.detail


def test_a_resumed_turn_is_unmeasured_against_the_final_snapshot(tmp_path: Path) -> None:
    t = _second_step(tmp_path, AGENTS.format(body="be terse\nlate rule"))
    (_snapshot(t, STEP) / "AGENTS.md").write_text("be terse\n")
    (_snapshot(t, SECOND) / "AGENTS.md").write_text("be terse\nlate rule\n")

    def resume(flow: dict[str, Any]) -> None:
        first = json.loads(json.dumps(flow["turns"][0]))
        first["session_audit"]["instructions"] = []
        flow["turns"] = [first, {**flow["turns"][0], "turn": 2}]

    _rewrite(_agent(t, SECOND), resume)
    check = _p8(t)

    assert check.status == "unmeasured"
    assert "resumed turn" in check.detail


def _place_component(t: HealthyTrial, text: str, sha256: str | None = None) -> None:
    agent = _agent(t)
    record = agent / "workflow" / "components" / "rules.md"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(text)
    digest = sha256 or hashlib.sha256(text.encode()).hexdigest()
    component = {"path": "AGENTS.md", "sha256": digest, "record": "workflow/components/rules.md"}
    _rewrite(agent, lambda flow: flow.update(components=[component]))


def test_a_component_record_vouches_for_its_lines_only_at_its_digest(tmp_path: Path) -> None:
    t = _session(tmp_path, AGENTS.format(body="placed rule"))
    _place_component(t, "placed rule\n")
    assert _p8(t).status == "pass"

    _place_component(t, "placed rule\n", sha256="0" * 64)
    found = _p8(t)

    assert found.status == "violation"
    assert "not the recorded digest" in found.detail


def test_a_turn_without_a_rollout_or_a_workflow_record_is_unmeasured(tmp_path: Path) -> None:
    t = _session(tmp_path, "solve the task")
    agent = _agent(t)
    shutil.rmtree(agent / "workflow" / "build-1")

    missing = _p8(t)
    assert missing.status == "unmeasured"
    assert missing.evidence["unmeasured"]

    (agent / WORKFLOW).write_text(json.dumps({"turns": []}))
    assert "workflow.json" in _p8(t).detail
    assert _p8(t).status == "unmeasured"


def test_a_changed_native_audit_source_is_a_violation(tmp_path: Path) -> None:
    t = _session(tmp_path, "solve the task")
    source = next(t.trial_dir.rglob("rollout-*.jsonl"))
    source.write_text(source.read_text() + "{}\n")

    check = _p8(t)

    assert check.status == "violation"
    assert "native audit source changed" in check.detail


def _claude_trial(
    tmp_path: Path, rule: str, skills: list[str], *, origin: str = "/app/CLAUDE.md"
) -> HealthyTrial:
    """A two-step trial whose second step ran claude; step one ended with CLAUDE.md = use uv."""
    t = healthy_trial(tmp_path, [StepOutcome(STEP), StepOutcome(SECOND)])
    agent = _agent(t, SECOND)
    turn_dir = agent / "workflow" / "build-1"
    stream = {
        "type": "system",
        "subtype": "init",
        "claude_code_version": "2.1.285",
        "skills": skills,
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}],
        "mcp_servers": [],
    }
    (turn_dir / "claude-code.txt").write_text(json.dumps(stream) + "\n")
    transcript = turn_dir / "sessions" / "projects" / "-app" / "claude.jsonl"
    transcript.parent.mkdir(parents=True)
    message = {
        "type": "user",
        "message": {"content": f"Contents of {origin} (project instructions):\n\n{rule}"},
    }
    system = {"type": "system", "content": "native harness instructions"}
    transcript.write_text(json.dumps(message) + "\n" + json.dumps(system) + "\n")
    (_snapshot(t, STEP) / "CLAUDE.md").write_text("use uv\n")
    turn = {
        "stage": "build",
        "turn": 1,
        "log_dir": "workflow/build-1",
        "session_audit": session_audit(Harness.CLAUDE_CODE, turn_dir),
    }
    (agent / WORKFLOW).write_text(json.dumps({"turns": [turn]}))
    return t


@pytest.mark.parametrize(
    ("rule", "skills", "status"),
    [
        ("use uv", [], "pass"),
        ("unrecorded host rule", [], "violation"),
        ("use uv", ["unknown-origin"], "unmeasured"),
    ],
)
def test_claude_native_audit_feeds_p8(
    tmp_path: Path, rule: str, skills: list[str], status: str
) -> None:
    check = _p8(_claude_trial(tmp_path, rule, skills))

    assert check.status == status, check.detail
    assert check.evidence["messages"]["claude-code@2.1.285"] == [
        {"role": "system", "sha256": hashlib.sha256(b"native harness instructions").hexdigest()}
    ]


def test_claude_instructions_are_checked_against_their_own_stored_file(tmp_path: Path) -> None:
    t = _claude_trial(tmp_path, "use uv\nonly the agents file has this", [])
    (_snapshot(t, STEP) / "AGENTS.md").write_text("only the agents file has this\n")

    check = _p8(t)

    assert check.status == "violation"
    assert "only the agents file has this" in check.detail


def test_a_claude_instruction_file_outside_the_workspace_is_a_violation(tmp_path: Path) -> None:
    check = _p8(_claude_trial(tmp_path, "use uv", [], origin="/root/.claude/CLAUDE.md"))

    assert check.status == "violation"
    assert "/root/.claude/CLAUDE.md" in check.detail


def _sealed_trial(tmp_path: Path) -> tuple[Path, Path, str]:
    """A trial directory of one small file, its record and the digest of its sealed manifest."""
    job, trial = tmp_path / "job", _trial_dir(tmp_path)
    (trial / "evidence").mkdir(parents=True)
    (trial / "evidence" / "a.txt").write_text("a")
    record = job / TRIAL_RECORDS_DIR / f"{TRIAL_ID}.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    return trial, record, seal_trial(trial, record, job)


def test_t2_the_sealed_records_manifest_must_verify(tmp_path: Path) -> None:
    trial, _, digest = _sealed_trial(tmp_path)
    audit = _audit(tmp_path, job=memory_job(records_manifests={TRIAL_ID: digest}))
    assert check_t2(audit).status == "pass"

    (trial / "health.json").write_text("{}")  # the audit's own output is not sealed
    assert check_t2(audit).status == "pass"

    (trial / "forged.txt").write_text("x")
    found = check_t2(audit)

    assert found.status == "violation"
    assert len(found.evidence["differences"]) == 1


def test_audit_trial_reads_the_result_and_each_attempted_steps_ledger_from_disk(
    tmp_path: Path,
) -> None:
    trial = _trial_dir(tmp_path)
    steps = (STEP, SECOND, "checkpoint_3")
    record = memory_trial(tuple(memory_step(name, index=i) for i, name in enumerate(steps)))
    write_step_record(step_record_dir(trial, STEP), memory_ledger(STEP))
    torn = step_record_dir(trial, SECOND)
    torn.mkdir(parents=True)
    (torn / RECORD_FILE).write_text("{torn")
    (trial / RESULT_FILE).write_text(json.dumps(memory_result(*steps)))

    report = audit_trial(tmp_path / "job", trial, record, memory_job())

    by_id = {check.id: check for check in report.checks}
    audited = {"V1", "V2", "V3", "H1", "H3", "H4", "H5", "H6", "H-target", "L1", "L2"}
    audited |= {"P2", "P3", "P8", "D1", "D2", "T1", "T2", "T3"}
    assert report.trial_id == TRIAL_ID
    assert audited <= set(by_id)
    assert by_id["V2"].evidence["cases"] == {STEP: len(memory_ledger().grade.cases)}
    assert by_id["H4"].status == "pass"


def test_the_records_manifest_does_not_vouch_for_a_missing_or_rewritten_manifest(
    tmp_path: Path,
) -> None:
    trial, record, digest = _sealed_trial(tmp_path)
    job = tmp_path / "job"
    assert tamper_findings(trial, record, job, digest) == []

    record.unlink()
    assert len(tamper_findings(trial, record, job, digest)) == 1

    record.write_text("{}")
    (trial / RECORDS_MANIFEST).write_text("{}\n")
    assert len(tamper_findings(trial, record, job, digest)) == 1

    (trial / RECORDS_MANIFEST).unlink()
    assert len(tamper_findings(trial, record, job, digest)) == 1


def _stored_smoke(
    root: Path, name: str, *, manifest: str, lock: str | None, record: str | None
) -> Path:
    job = root / "exp" / name
    (job / "trial-records").mkdir(parents=True)
    (job / "job.json").write_text(manifest)
    if lock is not None:
        (job / "job.lock.json").write_text(lock)
    if record is not None:
        (job / "trial-records" / f"{TRIAL_ID}.json").write_text(record)
    return job


def test_smoke_jobs_stored_under_the_jobs_root_clear_a_run_when_clean(tmp_path: Path) -> None:
    with TemporaryDirectory(dir=tmp_path) as temporary:
        root = Path(temporary)
        manifest = json.dumps(_smoke(_smoke_record()).manifest)
        lock = json.dumps(_lock("smoke"))
        record = _smoke_record().model_dump_json()
        good = _stored_smoke(root, "good", manifest=manifest, lock=lock, record=record)
        write_health(good / "trials" / TRIAL_ID, CLEAN_HEALTH)
        _stored_smoke(root, "torn", manifest="{torn", lock=lock, record=record)
        _stored_smoke(root, "listy", manifest="[]", lock=lock, record=record)
        _stored_smoke(root, "nolock", manifest=manifest, lock=None, record=record)
        benchmark = json.dumps(_lock("benchmark"))
        _stored_smoke(root, "bench", manifest=manifest, lock=benchmark, record=None)
        _stored_smoke(root, "norecord", manifest=manifest, lock=lock, record=None)
        _stored_smoke(root, "badrecord", manifest=manifest, lock=lock, record="{}")

        cleared = admit("ab-test", _wanted(), root, allow_unsmoked=False)
        assert cleared["smoke_job"] == "exp/good"
        assert smoke_problem(good, json.loads(manifest)) is None

        shutil.rmtree(good)
        overridden = admit("ab-test", _wanted(), root, allow_unsmoked=True)

        assert set(overridden["unmet"]) == {"exp/norecord", "exp/badrecord"}
        assert smoke_problem(root / "exp" / "norecord", json.loads(manifest)) is not None


def test_a_symlink_in_a_sealed_trial_is_evidence_and_retargeting_it_is_tampering(
    tmp_path: Path,
) -> None:
    job = tmp_path / "job"
    trial = job / "trials" / "t"
    trial.mkdir(parents=True)
    record = job / "trial-records" / "t.json"
    record.parent.mkdir()
    record.write_text("{}")
    (trial / "link").symlink_to("elsewhere")
    digest = seal_trial(trial, record, job)
    assert tamper_findings(trial, record, job, digest) == []

    (trial / "link").unlink()
    (trial / "link").symlink_to("other")

    assert tamper_findings(trial, record, job, digest) == ["trials/t/link changed"]
    assert tamper_findings(trial, record, job, None) != []
