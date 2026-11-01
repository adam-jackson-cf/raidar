"""Run-health decisions the audit tests leave open: tamper findings of the sealed records
manifest, liveness parsing and phase hooks, smoke admission over stored jobs, judge closure
readings, the ledger reader, ``raidar health`` and the offline-grading command wrappers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import click
import pytest
from click.testing import CliRunner
from harbor.trial.hooks import TrialEvent

from raidar.cli import grading as grading_cli
from raidar.cli import health as health_cli
from raidar.contract.harbor_result import RESULT_FILE
from raidar.engine.health import liveness
from raidar.engine.health.admission import (
    admit,
    identity,
    mismatches,
    smoke_problem,
)
from raidar.engine.health.base import Audit, read_ledger
from raidar.engine.health.fault_candidates import FAULT_CANDIDATES
from raidar.engine.health.liveness import (
    LIVENESS_REPORT,
    LivenessConfig,
    PhaseMonitor,
    idle_killed_steps,
    install_liveness_monitor,
    parse_poll_output,
)
from raidar.engine.health.model import (
    HEALTH_FILE,
    HealthCheck,
    HealthReport,
    load_health,
    write_health,
)
from raidar.engine.health.reaper import _KILLED, _RESIDUAL
from raidar.engine.health.records_manifest import (
    RECORDS_MANIFEST,
    current_files,
    seal_trial,
    tamper_findings,
)
from raidar.engine.health.workflow import check_workflow
from raidar.grading import offline
from raidar.grading.offline import OfflineGradingError
from raidar.grading.records import step_record_dir, write_step_record
from raidar.grading.regrade import RegradeOptions
from tests.health_support import (
    STEP,
    TRIAL_ID,
    memory_audit,
    memory_ledger,
    memory_result,
    memory_step,
    memory_trial,
)
from tests.test_agent_liveness_monitor import (
    NET_HEADER,
    ScriptedEnv,
    fake_trial,
    net_line,
    poll_output,
    stat_line,
)
from tests.test_health_judge import (
    CLEAN_HEALTH,
    RECIPES,
    RUNNER,
    TARGET,
    _agent,
    _checks,
    _lock,
    _save,
    _smoke_record,
    _step,
    _wanted,
)

# ---- the sealed records manifest ----------------------------------------------------------------


def _sealed_trial(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "job"
    trial = job / "trials" / "t1"
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "a.txt").write_text("a")
    (trial / RESULT_FILE).write_text("{}")
    record = job / "trial-records" / "t1.json"
    record.parent.mkdir()
    record.write_text("record")
    return job, trial, record


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def test_the_seal_lists_every_evidence_file_but_not_its_own_manifest_or_the_derived_audit(
    tmp_path: Path,
) -> None:
    job, trial, record = _sealed_trial(tmp_path)
    (trial / HEALTH_FILE).write_text("derived")
    (trial / "agent" / HEALTH_FILE).write_text("nested health is evidence")
    (trial / RECORDS_MANIFEST).write_text("stale")

    digest = seal_trial(trial, record, job)

    written = (trial / RECORDS_MANIFEST).read_bytes()
    assert digest == _sha(written)
    assert json.loads(written)["files"] == {
        "trial-records/t1.json": _sha("record"),
        "trials/t1/agent/a.txt": _sha("a"),
        "trials/t1/agent/health.json": _sha("nested health is evidence"),
        "trials/t1/result.json": _sha("{}"),
    }


def test_a_symlink_is_evidence_by_its_target_and_a_linked_directory_is_never_followed(
    tmp_path: Path,
) -> None:
    job, trial, record = _sealed_trial(tmp_path)
    os.symlink("a.txt", trial / "agent" / "file-link")
    os.symlink("../agent", trial / "agent" / "dir-link")
    os.symlink("nowhere", trial / "dangling")

    files = current_files(trial, record, job)

    assert files["trials/t1/agent/file-link"] == "symlink:" + _sha("a.txt")
    assert files["trials/t1/agent/dir-link"] == "symlink:" + _sha("../agent")
    assert files["trials/t1/dangling"] == "symlink:" + _sha("nowhere")
    assert not [path for path in files if "dir-link/" in path]


def test_a_trial_without_a_record_file_seals_only_its_directory(tmp_path: Path) -> None:
    job, trial, _ = _sealed_trial(tmp_path)

    files = current_files(trial, job / "trial-records" / "absent.json", job)

    assert set(files) == {"trials/t1/agent/a.txt", "trials/t1/result.json"}


def test_an_intact_trial_has_no_tamper_finding(tmp_path: Path) -> None:
    job, trial, record = _sealed_trial(tmp_path)
    digest = seal_trial(trial, record, job)

    assert tamper_findings(trial, record, job, digest) == []


def test_an_edited_removed_or_added_file_is_a_finding_in_that_order(tmp_path: Path) -> None:
    job, trial, record = _sealed_trial(tmp_path)
    digest = seal_trial(trial, record, job)
    (trial / "agent" / "a.txt").write_text("edited")
    (trial / RESULT_FILE).unlink()
    (trial / "agent" / "zz.txt").write_text("new")

    assert tamper_findings(trial, record, job, digest) == [
        "trials/t1/agent/a.txt changed",
        "trials/t1/result.json missing",
        "trials/t1/agent/zz.txt added",
    ]


def test_a_manifest_that_cannot_be_trusted_is_the_only_finding(tmp_path: Path) -> None:
    job, trial, record = _sealed_trial(tmp_path)

    assert tamper_findings(trial, record, job, None) == ["no records manifest digest in job.json"]
    assert tamper_findings(trial, record, job, "0" * 64) == [f"{RECORDS_MANIFEST} is missing"]
    digest = seal_trial(trial, record, job)
    (trial / RECORDS_MANIFEST).write_text("{}\n")
    assert tamper_findings(trial, record, job, digest) == [
        f"{RECORDS_MANIFEST} differs from the digest in job.json"
    ]


# ---- liveness polls, run loop and phase hooks ---------------------------------------------------


def test_a_process_line_with_unreadable_cpu_counters_is_skipped_not_fatal() -> None:
    broken = "9 (odd) S 1 1 1 0 -1 0 0 0 0 0 x 0 0 0 20 0 1 0 5 0 0"
    reading = parse_poll_output(poll_output([broken, stat_line(5, "ok", 3)]))

    assert reading is not None
    assert reading.procs == {5: ("ok", 3)}


def test_a_network_table_with_unreadable_counters_is_unmeasured() -> None:
    text = "\n".join(["@@CLK 100", "@@PROC", "@@NET", NET_HEADER.rstrip()])
    text += "\n" + net_line("eth0", "x", 5) + "\n@@END\n"

    assert parse_poll_output(text) is None


def test_a_clock_rate_that_is_not_a_number_is_unmeasured() -> None:
    assert parse_poll_output(poll_output([]).replace("@@CLK 100", "@@CLK fast")) is None


class TickClock:
    """A monotonic clock that advances ``step`` seconds on every reading."""

    def __init__(self, step: float = 10.0, fail_after: int | None = None) -> None:
        self.now, self.step, self.fail_after, self.reads = 1000.0, step, fail_after, 0

    def __call__(self) -> float:
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise RuntimeError("clock lost")
        self.now += self.step
        return self.now


class ReapingEnv(ScriptedEnv):
    """Quiet polls, and a successful kill report for the reaper script."""

    async def exec(self, command: str, **kwargs: Any) -> Any:
        if "kill -KILL" in command:
            self.calls.append({"command": command, **kwargs})
            stdout = f"{_KILLED}\n5 1000 S sleep\n{_RESIDUAL}\n"
            return SimpleNamespace(stdout=stdout, stderr="", return_code=0)
        return await super().exec(command, **kwargs)


class _Yield:
    """Suspends the running task for one event-loop turn (no timer, no sleep)."""

    def __await__(self) -> Any:
        yield


async def _turn(*_ignored: Any) -> None:
    await _Yield()


@pytest.fixture(autouse=True)
def _injected_loop_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sampling loop's pause between polls is one loop turn, not a timer."""
    names = ("ensure_future", "shield", "create_task", "gather", "CancelledError")
    patched = SimpleNamespace(**{name: getattr(asyncio, name) for name in names}, sleep=_turn)
    monkeypatch.setattr(liveness, "asyncio", patched)


def test_the_sampling_loop_flushes_and_stops_at_an_enforced_idle_kill() -> None:
    quiet = poll_output([stat_line(5, "sleep", 3)])
    config = LivenessConfig("enforce", interval_s=0, idle_s=45)
    monitor = PhaseMonitor(config, ReapingEnv([quiet]), "agent", clock=TickClock())
    flushed: list[str] = []

    asyncio.run(monitor.run(lambda: flushed.append(monitor.verdict.decision)))

    assert monitor.stopped
    assert flushed == ["idle_killed"]
    assert monitor.error is None
    asyncio.run(monitor.run(lambda: flushed.append("again")))  # a stopped phase is not resumed
    assert flushed == ["idle_killed"]


def test_the_sampling_loop_flushes_every_twentieth_sample_and_records_why_it_died() -> None:
    quiet = poll_output([stat_line(5, "sleep", 3)])
    config = LivenessConfig("observe", interval_s=0, idle_s=10**9)
    monitor = PhaseMonitor(config, ScriptedEnv([quiet]), "agent", clock=TickClock())
    counts: list[int] = []

    def flush() -> None:
        counts.append(monitor.sample_count)
        raise OSError("disk full")

    asyncio.run(monitor.run(flush))

    assert counts == [20]
    assert monitor.error == "OSError: disk full"


def test_a_clock_failure_ends_the_loop_with_the_error_recorded_never_raised() -> None:
    config = LivenessConfig("observe", interval_s=0, idle_s=10**9)
    clock = TickClock(fail_after=4)
    monitor = PhaseMonitor(config, ScriptedEnv([poll_output([])]), "agent", clock=clock)

    asyncio.run(monitor.run(lambda: None))

    assert monitor.error == "RuntimeError: clock lost"


def _event(result: Any) -> Any:
    return SimpleNamespace(result=result)


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "trial" / LIVENESS_REPORT).read_text())


def test_output_outside_an_agent_phase_never_counts_and_the_phase_is_labelled_by_its_step(
    tmp_path: Path,
) -> None:
    trial, hooks, callbacks = fake_trial(tmp_path, ScriptedEnv([poll_output([])]))
    install_liveness_monitor(
        cast(Any, trial), LivenessConfig("observe", interval_s=0, idle_s=10**9)
    )
    start, end = hooks[TrialEvent.AGENT_START][0], hooks[TrialEvent.AGENT_END][0]
    done = _event(SimpleNamespace(step_results=[SimpleNamespace(step_name="checkpoint_1")]))

    async def phase() -> None:
        await callbacks[0](SimpleNamespace(phase="agent"))  # before the phase: no monitor yet
        await start(done)
        for _ in range(3):
            await _turn()
        await callbacks[0](SimpleNamespace(phase="agent_setup"))
        await _turn()
        await callbacks[0](SimpleNamespace(phase="agent"))
        for _ in range(2):
            await _turn()
        await end(done)

    asyncio.run(phase())

    (recorded,) = _report(tmp_path)["phases"]
    assert recorded["step"] == "checkpoint_1"
    chunks = [s["output_chunks"] for s in recorded["samples"] if "output_chunks" in s]
    assert sum(chunks) == 1


@pytest.mark.parametrize(
    "result",
    [None, SimpleNamespace(step_results=[]), SimpleNamespace(step_results=[SimpleNamespace()])],
    ids=["no-result", "no-steps", "unnamed-step"],
)
def test_a_phase_whose_step_cannot_be_named_is_recorded_without_a_label(
    tmp_path: Path, result: Any
) -> None:
    trial, hooks, _ = fake_trial(tmp_path, ScriptedEnv([poll_output([])]))
    install_liveness_monitor(
        cast(Any, trial), LivenessConfig("observe", interval_s=0, idle_s=10**9)
    )

    async def phase() -> None:
        await hooks[TrialEvent.AGENT_START][0](_event(result))
        await _turn()
        await hooks[TrialEvent.AGENT_END][0](_event(result))

    asyncio.run(phase())

    (recorded,) = _report(tmp_path)["phases"]
    assert recorded["step"] is None


def test_an_agent_end_without_a_start_writes_a_report_with_no_phases(tmp_path: Path) -> None:
    trial, hooks, _ = fake_trial(tmp_path, ScriptedEnv([poll_output([])]))
    install_liveness_monitor(cast(Any, trial), LivenessConfig("observe"))
    asyncio.run(hooks[TrialEvent.AGENT_END][0](_event(None)))

    assert _report(tmp_path)["phases"] == []


def test_a_task_without_an_agent_user_is_sampled_as_unmeasured_never_idle(tmp_path: Path) -> None:
    env = ScriptedEnv([poll_output([])])
    trial, hooks, _ = fake_trial(tmp_path, env)
    trial.task.config.agent.user = None
    install_liveness_monitor(
        cast(Any, trial), LivenessConfig("enforce", interval_s=0, idle_s=0.001)
    )

    async def phase() -> None:
        await hooks[TrialEvent.AGENT_START][0](_event(None))
        for _ in range(3):
            await _turn()
        await hooks[TrialEvent.AGENT_END][0](_event(None))

    asyncio.run(phase())

    (recorded,) = _report(tmp_path)["phases"]
    assert recorded["decision"] == "active"
    assert all("unmeasured" in s for s in recorded["samples"])
    assert env.calls == []


def test_an_enforcing_monitor_arms_the_output_callback_and_both_phase_hooks(
    tmp_path: Path,
) -> None:
    trial, hooks, callbacks = fake_trial(tmp_path, ScriptedEnv([""]))
    install_liveness_monitor(cast(Any, trial), LivenessConfig("enforce"))

    assert len(callbacks) == 1
    assert {event.name for event in hooks} == {"AGENT_START", "AGENT_END"}


def test_only_enforced_idle_kills_name_steps_and_an_unreadable_report_names_none(
    tmp_path: Path,
) -> None:
    assert idle_killed_steps(tmp_path) == frozenset()
    report = tmp_path / LIVENESS_REPORT
    report.write_text("{torn")
    assert idle_killed_steps(tmp_path) == frozenset()
    report.write_text("[]")
    assert idle_killed_steps(tmp_path) == frozenset()
    report.write_text(
        json.dumps(
            {
                "phases": [
                    {"step": "checkpoint_1", "decision": "idle_killed"},
                    {"step": "checkpoint_2", "decision": "idle_observed"},
                    {"step": None, "decision": "idle_killed"},
                    "stray",
                ]
            }
        )
    )

    assert idle_killed_steps(tmp_path) == frozenset({"checkpoint_1"})


# ---- smoke admission over stored jobs -----------------------------------------------------------


MANIFEST: dict[str, Any] = {
    "runner": RUNNER,
    "recipes": RECIPES,
    "execution_target": TARGET,
    "selected": [TRIAL_ID],
    "finished_at": "2026-01-01T00:00:00+00:00",
}


def _stored(root: Path, name: str, manifest: str, lock: dict[str, Any] | None = None) -> Path:
    """A stored job holding only its manifest (and lock): a few bytes, no trial files."""
    job = root / name
    job.mkdir(parents=True)
    (job / "job.json").write_text(manifest)
    if lock is not None:
        (job / "job.lock.json").write_text(json.dumps(lock))
    return job


def test_only_stored_smoke_jobs_with_readable_manifest_and_lock_are_searched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, valid = tmp_path / "jobs", json.dumps(MANIFEST)
    contents: dict[Path, str] = {}
    for name, manifest, lock in (
        ("measured", valid, _lock("ab-test")),
        ("no-lock", valid, None),
        ("torn-manifest", "{torn", _lock("smoke")),
        ("list-manifest", "[1]", _lock("smoke")),
        ("smoke-1", valid, _lock("smoke")),
    ):
        job = root / "exp" / name
        contents[job / "job.json"] = manifest
        if lock is not None:
            contents[job / "job.lock.json"] = json.dumps(lock)
    original_glob, original_read = Path.glob, Path.read_text

    def glob(path: Path, pattern: str):
        if path == root:
            return (item for item in contents if item.name == "job.json")
        return original_glob(path, pattern)

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        return contents[path] if path in contents else original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(Path, "read_text", read)
    overridden = admit("ab-test", _wanted(), root, allow_unsmoked=True)

    assert overridden["smoke_job"] is None
    assert list(overridden["unmet"]) == ["exp/smoke-1"]


def test_a_stored_smoke_is_clean_only_with_a_readable_record_and_a_health_audit(
    tmp_path: Path,
) -> None:
    job = _stored(tmp_path, "smoke", json.dumps(MANIFEST))
    record = job / "trial-records" / f"{TRIAL_ID}.json"
    record.parent.mkdir()

    assert "no record" in (smoke_problem(job, MANIFEST) or "")
    record.write_text("{torn")
    assert "unreadable record" in (smoke_problem(job, MANIFEST) or "")
    stored = _smoke_record()
    record.write_text(stored.model_dump_json())
    assert "health.json" in (smoke_problem(job, MANIFEST) or "")
    write_health(job / stored.trial_dir, CLEAN_HEALTH)
    assert smoke_problem(job, MANIFEST) is None


def test_a_smoke_must_have_smoked_every_arm_identity_the_run_wants() -> None:
    wanted = _wanted(_agent(), _agent("terra", model="gpt-6-terra"))
    smoked = _wanted(_agent())
    runner = {**RUNNER, "toolchain_sha256": "x" * 64}
    other_runner = identity(_lock("smoke"), runner, RECIPES, TARGET)
    assert other_runner is not None

    assert mismatches(wanted, wanted) == []
    uncovered = mismatches(wanted, smoked)
    terra = next(arm for arm in wanted["arms"] if arm["model"] == "gpt-6-terra")
    assert len(uncovered) == 1
    assert str([terra]) in uncovered[0]
    assert mismatches(smoked, other_runner) == ["toolchain_sha256 differs"]


# ---- judge closure readings and the workflow slice ----------------------------------------------


def _violated(root: Path, check: str) -> str:
    found = _checks(root)[check]
    assert found.status == "violation"
    return found.detail


def test_a_probability_that_differs_from_the_retained_answer_is_a_violation(
    tmp_path: Path,
) -> None:
    verify = _step(tmp_path)
    verify["judge_calls"][0]["probability"] = 0.1
    _save(tmp_path, verify)

    _violated(tmp_path, "J1")


def test_a_malformed_ledger_entry_is_unmeasured(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judge_calls"] = [None]
    _save(tmp_path, verify)

    found = _checks(tmp_path)["J1"]

    assert found.status == "unmeasured"


def test_a_ledger_entry_out_of_sequence_is_a_violation(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judge_calls"][0]["index"] = 5
    _save(tmp_path, verify)

    _violated(tmp_path, "J1")


def test_an_aspect_whose_call_list_is_not_a_list_has_no_call_to_check(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judged"][0]["criteria"][0]["aspects"][0]["calls"] = "0"
    _save(tmp_path, verify)

    found = _checks(tmp_path)["J2"]

    assert found.status == "unmeasured"


def test_an_aspect_that_was_never_asked_owes_no_call(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["ending"] = "pass_limit"
    passed = verify["judged"][0]
    passed["criteria"][0]["aspects"][0] |= {"status": "not_asked", "calls": []}
    passed["criteria"][0]["status"] = "undecided"
    passed["closed"], passed["undecided"] = [], ["F1.AC1"]
    _save(tmp_path, verify)

    assert _checks(tmp_path)["J2"].status == "pass"


def test_an_aspect_cannot_cite_another_aspects_answer(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judge_calls"][0]["aspect"] = "F9.AC9.A9"
    _save(tmp_path, verify)

    _violated(tmp_path, "J2")


def test_a_non_object_retained_result_cannot_support_a_recorded_probability(
    tmp_path: Path,
) -> None:
    verify = _step(tmp_path)
    verify["judge_calls"][0]["result"] = "oops"
    _save(tmp_path, verify)

    _violated(tmp_path, "J2")


def test_a_failed_precheck_leaves_a_criterion_open(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    passed = verify["judged"][0]
    passed["criteria"][0] |= {"failing": ["precheck"], "status": "open"}
    passed["closed"], passed["open"] = [], ["F1.AC1"]
    _save(tmp_path, verify)

    assert _checks(tmp_path)["J2"].status == "pass"
    verify["judged"][0]["criteria"][0]["status"] = "closed"
    _save(tmp_path, verify)
    _violated(tmp_path, "J2")


def test_a_pass_summary_must_list_the_criteria_it_closed(tmp_path: Path) -> None:
    verify = _step(tmp_path)
    verify["judged"][0]["closed"] = []
    _save(tmp_path, verify)

    _violated(tmp_path, "J2")


def test_the_workflow_slice_audits_the_judge_then_the_lab_owned_evidence(tmp_path: Path) -> None:
    trial_dir = tmp_path / "job" / "trials" / TRIAL_ID
    _step(trial_dir / "steps" / STEP / "agent")

    checks = check_workflow(memory_audit(trial_dir))

    assert [c.id for c in checks if c.family == "J"] == ["J1", "J2", "J3"]
    assert {c.status for c in checks if c.family == "J"} == {"pass"}
    assert "W1" in {c.id for c in checks}


# ---- the verifier ledger as the audit reads it --------------------------------------------------


def test_a_step_ledger_is_read_when_valid_and_absent_when_missing_or_torn(tmp_path: Path) -> None:
    ledger = memory_ledger()
    write_step_record(step_record_dir(tmp_path, STEP), ledger)
    torn = step_record_dir(tmp_path, "checkpoint_2")
    torn.mkdir(parents=True)
    (torn / "record.json").write_text("{torn")

    assert read_ledger(tmp_path, STEP) == ledger
    assert read_ledger(tmp_path, "checkpoint_2") is None
    assert read_ledger(tmp_path, "checkpoint_3") is None


def test_an_audit_reads_harbors_result_and_each_attempted_steps_ledger_from_disk(
    tmp_path: Path,
) -> None:
    trial_dir = tmp_path / "job" / "trials" / TRIAL_ID
    trial_dir.mkdir(parents=True)
    ledger = memory_ledger()
    write_step_record(step_record_dir(trial_dir, STEP), ledger)
    result = memory_result(STEP, "checkpoint_2")
    (trial_dir / RESULT_FILE).write_text(json.dumps(result))
    record = memory_trial((memory_step(STEP, ledger), memory_step("checkpoint_2", index=1)))

    audit = Audit.from_disk(tmp_path / "job", trial_dir, record, {})

    assert audit.result == result
    assert audit.ledger(STEP) == ledger
    assert audit.ledger("checkpoint_2") is None
    assert [step.step for step, _ in audit.graded()] == [STEP]


# ---- raidar health ------------------------------------------------------------------------------


def _report_of(trial_id: str, *checks: HealthCheck) -> HealthReport:
    return HealthReport(trial_id=trial_id, checks=checks)


def _check(check_id: str, status: Any, detail: str = "") -> HealthCheck:
    return HealthCheck(id=check_id, family=check_id[0], status=status, detail=detail)


def _health_job(tmp_path: Path, *trial_ids: str, manifest: bool = True) -> Path:
    job = tmp_path / "job"
    (job / "trial-records").mkdir(parents=True)
    if manifest:
        (job / "job.json").write_text("{}")
    for trial_id in trial_ids:
        record = memory_trial((memory_step(),), trial_dir=f"trials/{trial_id}")
        (job / "trial-records" / f"{trial_id}.json").write_text(record.model_dump_json())
    return job


@pytest.mark.parametrize("trials", [(), ("t1",)], ids=["no-records", "no-manifest"])
def test_a_directory_that_is_not_a_job_with_trial_records_is_refused(
    tmp_path: Path, trials: tuple[str, ...]
) -> None:
    job = _health_job(tmp_path, *trials, manifest=bool(not trials))

    result = CliRunner().invoke(health_cli.health_command, [str(job)])

    assert result.exit_code == 1
    assert "is not a job directory with trial records" in result.output


def test_a_job_that_does_not_exist_is_a_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(health_cli.health_command, [str(tmp_path / "gone")])

    assert result.exit_code == 2


def _audits(monkeypatch: pytest.MonkeyPatch, reports: dict[str, HealthReport]) -> None:
    def audit(job_dir: Path, trial_dir: Path, record: Any, manifest: Any) -> HealthReport:
        return reports[trial_dir.name]

    monkeypatch.setattr(health_cli, "audit_trial", audit)


def test_health_rewrites_each_trials_audit_and_fails_the_command_on_any_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _health_job(tmp_path, "bad", "ok")
    _audits(
        monkeypatch,
        {
            "ok": _report_of("ok", _check("V1", "pass")),
            "bad": _report_of(
                "bad", _check("T2", "violation", "tampered"), _check("H3", "unmeasured", "none")
            ),
        },
    )

    result = CliRunner().invoke(health_cli.health_command, [str(job)])

    assert result.exit_code == 1
    bad, ok = load_health(job / "trials" / "bad"), load_health(job / "trials" / "ok")
    assert bad is not None
    assert ok is not None
    assert [c.id for c in bad.violations] == ["T2"]
    assert [c.id for c in bad.unmeasured] == ["H3"]
    assert ok.violations == ()
    assert ok.unmeasured == ()
    assert all(word in result.output for word in ("bad", "ok", "T2", "H3", "V1"))
    stored = [json.loads(line) for line in (job / FAULT_CANDIDATES).read_text().splitlines()]
    assert [(line["trial"], line["check_id"]) for line in stored] == [("bad", "T2")]


def test_rerunning_health_on_a_violating_job_never_duplicates_its_fault_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _health_job(tmp_path, "bad")
    _audits(monkeypatch, {"bad": _report_of("bad", _check("T2", "violation", "tampered"))})

    for _ in range(2):
        assert CliRunner().invoke(health_cli.health_command, [str(job)]).exit_code == 1

    assert len((job / FAULT_CANDIDATES).read_text().splitlines()) == 1


def test_a_job_without_violations_exits_clean_and_records_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _health_job(tmp_path, "ok")
    _audits(monkeypatch, {"ok": _report_of("ok", _check("V1", "pass"))})

    result = CliRunner().invoke(health_cli.health_command, [str(job)])

    assert result.exit_code == 0
    assert not (job / FAULT_CANDIDATES).exists()


# ---- offline grading command wrappers -----------------------------------------------------------

OPTIONS = RegradeOptions(docker_context="ctx")
PATHS = (Path("target"), Path("store"))

WRAPPERS = {
    "regrade": lambda: grading_cli.regrade(PATHS[0], OPTIONS, None),
    "grade_pre_fix": lambda: grading_cli.grade_pre_fix(
        *PATHS, ["ref"], OPTIONS, replay=False, concurrency=2
    ),
    "produce_coverage": lambda: grading_cli.produce_coverage(
        *PATHS, ["ref"], OPTIONS, replay=True, concurrency=2
    ),
    "check_coverage": lambda: grading_cli.check_coverage(PATHS[0], OPTIONS, None, 2),
}


@pytest.mark.parametrize("name", WRAPPERS)
def test_an_offline_grading_run_that_cannot_proceed_is_a_command_error(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    def cannot(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OfflineGradingError("grader image missing")

    monkeypatch.setattr(offline, name, cannot)

    with pytest.raises(click.ClickException, match="grader image missing"):
        WRAPPERS[name]()
