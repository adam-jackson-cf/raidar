"""Fast N1 decisions: timings, percentile boundaries, contention and CLI policy."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.contract.trial import (
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
)
from raidar.engine.perf.budgets import (
    N1_REFERENCE_BUDGETS,
    Budget,
    JobSamples,
    PerfInputError,
    PhaseCheck,
    TrialSamples,
    Verdict,
    budget_set_for,
    check_budgets,
    load_job_samples,
    percentile,
)
from raidar.engine.perf.contention import Contention, TrialWindow, peak_during, peak_overlap
from raidar.engine.perf.phases import phase_samples, trial_phases
from raidar.engine.perf.report import PerfReport, build_report, report_from_samples
from raidar.grading.records import PRE_AGENT_LEAK_FILE, RECORD_FILE, step_record_dir
from tests.execution_support import StepOutcome, _record


def _samples(phase: str, values: list[float], condition: str | None = None) -> list[PhaseSample]:
    return [PhaseSample(phase=phase, seconds=v, condition=condition) for v in values]


def _timeline(steps: int = 2) -> tuple[dict, dict]:
    origin = datetime(2026, 10, 1, tzinfo=UTC)

    def at(seconds: float) -> str:
        return (origin + timedelta(seconds=seconds)).isoformat()

    result = {
        "environment_setup": {"started_at": at(0), "finished_at": at(10)},
        "agent_setup": {"started_at": at(10), "finished_at": at(11)},
        "finished_at": at(14 + steps * 20),
        "step_results": [],
    }
    readings = {}
    for index in range(steps):
        name = f"checkpoint_{index + 1}"
        clock = 11 + index * 20
        result["step_results"].append(
            {
                "step_name": name,
                "agent_execution": {"started_at": at(clock + 2), "finished_at": at(clock + 12)},
                "verifier": {"started_at": at(clock + 15), "finished_at": at(clock + 20)},
            }
        )
        readings[name] = (
            0.5,
            0.24,
            [
                PhaseSample(phase=phase, seconds=seconds, step=name, condition=condition)
                for phase, seconds, condition in (
                    ("snapshot_transfer", 0.01, "artifact"),
                    ("verifier_plumbing", 0.2, None),
                    ("grader_image", 0.3, "warm"),
                )
            ],
        )
    return result, readings


def _phases(*, steps: int = 2, seed: float | None = None, cold: bool = False) -> list[PhaseSample]:
    result, readings = _timeline(steps)
    return phase_samples(
        result,
        readings,
        arm_apply_s=0.25,
        environment_cold=cold,
        compose_init=not cold,
        workspace_seed_s=seed,
    )


def test_phase_accounting_separates_leaks_seed_and_unbudgeted_work() -> None:
    phases = {(s.phase, s.step): s for s in _phases()}
    assert phases[("environment_start", None)].seconds == 10.0
    assert phases[("environment_start", None)].condition == "warm"
    assert phases[("arm_apply", None)].seconds == 0.25
    assert phases[("per_step_reset", "checkpoint_1")].seconds == pytest.approx(1.5)
    assert phases[("pre_agent_leak_check", "checkpoint_1")].seconds == 0.5
    assert phases[("setup_sh", "checkpoint_1")].seconds == 0.24
    assert phases[("artifact_collection", "checkpoint_1")].seconds == 3.0
    assert phases[("snapshot_transfer", "checkpoint_1")].condition == "artifact"
    assert phases[("verifier_plumbing", "checkpoint_2")].seconds == 0.2
    assert phases[("step_archive_gap", "checkpoint_1")].seconds == pytest.approx(1.5)
    assert ("step_archive_gap", "checkpoint_2") not in phases
    assert phases[("teardown_result_write", None)].condition == "init"
    assert phases[("per_step_overhead", "checkpoint_1")].seconds == pytest.approx(6.21)
    assert phases[("per_trial_fixed_overhead", None)].seconds == pytest.approx(13.25)
    assert phases[("per_trial_fixed_overhead", None)].condition == "init"
    assert ("workspace_seed", None) not in phases
    for seed, environment in ((4.0, 6.0), (30.0, 0.0)):
        seeded = {(s.phase, s.step): s for s in _phases(seed=seed, cold=True)}
        assert seeded[("workspace_seed", None)].seconds == seed
        assert seeded[("environment_start", None)].seconds == environment
        assert seeded[("environment_start", None)].condition == "cold"
        assert seeded[("per_trial_fixed_overhead", None)].seconds == environment + 0.25 + 3.0
        assert seeded[("per_trial_fixed_overhead", None)].condition == "cold"
    cold = {(s.phase, s.step): s for s in _phases(cold=True)}
    assert cold[("teardown_result_write", None)].condition == "no-init"


def test_missing_timestamps_do_not_invent_durations_or_complete_aggregates() -> None:
    assert phase_samples({}, {}, arm_apply_s=0.2, environment_cold=False, compose_init=True) == [
        PhaseSample(phase="arm_apply", seconds=0.2)
    ]
    result, _ = _timeline()
    result["step_results"] = [None, {"step_name": "a"}, {"step_name": "b"}]
    samples = phase_samples(
        result,
        {"a": (0.0, None, []), "b": (0.0, None, [])},
        arm_apply_s=0,
        environment_cold=False,
        compose_init=True,
    )
    assert {s.phase for s in samples} == {"environment_start", "agent_setup", "arm_apply"}


def _passes(checks: list[PhaseCheck]) -> bool:
    """Whether the perf report over ``checks`` passes, with no contention splits."""
    return PerfReport(checks, _contention(), {}).passed


def test_p95_over_enough_samples_decides_the_verdict() -> None:
    budget = (Budget("per_step_reset", 2.0),)
    under = check_budgets(_samples("per_step_reset", [1.0] * 9 + [2.5]), budgets=budget)
    assert percentile([1.0] * 9 + [2.5], 0.95) == pytest.approx(1.825)
    assert under[0].verdict is Verdict.PASS
    over = check_budgets(_samples("per_step_reset", [1.0] * 8 + [2.5, 2.5]), budgets=budget)
    assert over[0].verdict is Verdict.FAIL
    few = check_budgets(_samples("per_step_reset", [1.0] * 9), budgets=budget)
    assert few[0].verdict is Verdict.INSUFFICIENT
    assert not _passes(few)
    assert _passes(
        check_budgets(_samples("per_step_reset", [1.0] * 9), budgets=budget, min_samples=5)
    )
    assert (
        check_budgets(_samples("per_step_reset", [2.0] * 10), budgets=budget)[0].verdict
        is Verdict.PASS
    )
    assert percentile([], 0.95) is None


def test_conditions_select_budgets_and_cold_is_a_tripwire() -> None:
    samples = (
        _samples("snapshot_transfer", [1.5] * 10, "download")
        + _samples("environment_start", [300.0], "cold")
        + _samples("teardown_result_write", [10.0] * 10, "no-init")
    )
    checks = {(c.budget.phase, c.budget.condition): c for c in check_budgets(samples)}
    assert checks[("snapshot_transfer", "download")].verdict is Verdict.PASS
    assert ("snapshot_transfer", "artifact") not in checks
    assert checks[("environment_start", "cold")].verdict is Verdict.PASS
    assert checks[("teardown_result_write", "no-init")].verdict is Verdict.PASS
    assert checks[("per_step_reset", None)].verdict is Verdict.MISSING
    for seconds, verdict in ((420, Verdict.PASS), (421, Verdict.FAIL)):
        tripped = check_budgets(_samples("environment_start", [seconds], "cold"))
        assert next(c for c in tripped if c.budget.phase == "environment_start").verdict is verdict


def test_between_step_budgets_apply_only_when_a_trial_ran_two_checkpoints() -> None:
    budgets = (Budget("per_step_reset", 2.0), Budget("step_archive_gap", 2.5, multi_step_only=True))
    samples = _samples("per_step_reset", [1.0] * 10)
    one_shot = check_budgets(samples, budgets=budgets, multi_step=False)
    assert [c.verdict for c in one_shot] == [Verdict.PASS, Verdict.NOT_APPLICABLE]
    assert _passes(one_shot)
    multi = check_budgets(samples, budgets=budgets, multi_step=True)
    assert multi[1].verdict is Verdict.MISSING
    assert not _passes(multi)


def _window(start: float, end: float, trial: str = "t") -> TrialWindow:
    origin = datetime(2026, 10, 1, tzinfo=UTC)
    return TrialWindow(
        "r", trial, origin + timedelta(seconds=start), origin + timedelta(seconds=end)
    )


def test_peak_overlap_counts_open_windows_and_touching_windows_do_not_overlap() -> None:
    windows = [_window(0, 10, "a"), _window(10, 20, "b"), _window(5, 15, "c"), _window(6, 7, "d")]
    assert peak_overlap([]) == 0
    assert peak_overlap(windows[:2]) == 1
    assert peak_overlap(windows) == 3
    assert peak_during(windows[1], windows) == 2
    assert peak_during(windows[0], windows) == 3


def _loaded(*, teardown: float = 1, target: str | None = None, steps: int = 2) -> JobSamples:
    trials = tuple(
        TrialSamples(
            "r",
            str(index),
            Path("unused"),
            tuple(
                sample
                if sample.phase != "teardown_result_write"
                else sample.model_copy(update={"seconds": teardown if index >= 6 else 1.0})
                for sample in [
                    *_phases(steps=steps),
                    PhaseSample(phase="raidar_result_write", seconds=0.001),
                ]
            ),
        )
        for index in range(10)
    )
    return JobSamples(
        [s for t in trials for s in t.samples], len(trials), steps > 1, trials, target
    )


def _contention() -> Contention:
    return Contention(
        {"r": 4},
        4,
        4,
        (),
        0,
        {("r", str(index)): 1 if index < 6 else 4 for index in range(10)},
    )


def test_contention_splits_only_breached_non_tripwire_budgets() -> None:
    report = report_from_samples(_loaded(teardown=6), _contention(), min_samples=3)
    check_index = next(
        i for i, c in enumerate(report.checks) if c.budget.phase == "teardown_result_write"
    )
    split = {s.label: s.check for s in report.splits[check_index]}
    assert split["<=3"].verdict is Verdict.PASS
    assert split["<=3"].samples == 6
    assert split[">=4"].verdict is Verdict.FAIL
    assert split[">=4"].p95 == 6.0
    assert not report.passed
    assert report_from_samples(_loaded(), _contention(), min_samples=3).passed
    untimed = Contention({"r": None}, 0, 0, (), 10)
    empty = report_from_samples(_loaded(teardown=6), untimed, min_samples=3)
    assert all(splits == [] for splits in empty.splits.values())
    cold = _loaded()
    cold.samples.append(PhaseSample(phase="environment_start", seconds=421, condition="cold"))
    tripped = report_from_samples(cold, _contention(), min_samples=3)
    tripwire_index = next(i for i, c in enumerate(tripped.checks) if c.budget.tripwire)
    assert tripped.checks[tripwire_index].verdict is Verdict.FAIL
    assert tripwire_index not in tripped.splits


@pytest.mark.parametrize("target", [None, "unaccepted", "mbp"])
def test_target_budget_selection_judges_samples_not_labels(target: str | None) -> None:
    samples = _samples("artifact_collection", [3.0] * 10)
    checks = check_budgets(samples, budgets=budget_set_for(target).budgets)
    assert next(c for c in checks if c.budget.phase == "artifact_collection").verdict is (
        Verdict.FAIL if target == "mbp" else Verdict.PASS
    )
    reference = {(b.phase, b.condition): b for b in N1_REFERENCE_BUDGETS}
    selected = {(b.phase, b.condition): b for b in budget_set_for(target).budgets}
    for key in (("per_trial_fixed_overhead", "no-init"), ("environment_start", "cold")):
        assert selected[key] == reference[key]


@pytest.mark.parametrize("strict", [False, True])
def test_cli_budget_breaches_warn_by_default_and_fail_strictly(
    monkeypatch, tmp_path: Path, strict: bool
) -> None:
    loaded = _loaded(teardown=6)
    report = report_from_samples(loaded, _contention(), min_samples=3)
    monkeypatch.setattr("raidar.cli.perf.load_job_samples", lambda _: loaded)
    monkeypatch.setattr("raidar.cli.perf.build_report", lambda *a, **kw: report)
    flags = ["--strict"] if strict else []
    result = CliRunner().invoke(main, ["perf", "check", *flags, "--json", str(tmp_path)])
    assert result.exit_code == int(strict), result.output
    payload = json.loads(result.output)
    assert payload["strict"] is strict
    assert payload["passed"] is False
    warned = {(w["phase"], w["condition"]): w for w in payload["warnings"]}
    assert warned[("teardown_result_write", "init")]["verdict"] == "fail"
    assert warned[("teardown_result_write", "init")]["p95_s"] == 6.0
    assert payload["contention"]["peak_concurrent_trials"] == 4
    text = CliRunner().invoke(main, ["perf", "check", *flags, str(tmp_path)])
    assert text.exit_code == int(strict)
    assert "WARNING fail" in text.output
    assert "at >=4 concurrent: fail" in text.output
    assert ("pass --strict to fail on them" in text.output) is not strict


@pytest.mark.parametrize("malformed", [False, True])
def test_cli_refuses_empty_and_malformed_inputs_even_report_only(
    monkeypatch, tmp_path: Path, malformed: bool
) -> None:
    def load(_):
        if malformed:
            raise PerfInputError("unreadable trial record")
        return JobSamples([], 0, False)

    monkeypatch.setattr("raidar.cli.perf.load_job_samples", load)
    result = CliRunner().invoke(main, ["perf", "check", str(tmp_path)])
    assert result.exit_code == 1
    assert ("unreadable trial record" if malformed else "no trial records") in result.output


def test_cli_one_shot_report_preserves_contention_and_record_only_tripwires(
    monkeypatch, tmp_path: Path
) -> None:
    loaded = _loaded(steps=1)
    loaded.samples.append(PhaseSample(phase="environment_start", seconds=300, condition="cold"))
    contention = Contention({"r": None}, 1, 3, ("busy",), 1, {}, ("unknown",))
    report = report_from_samples(loaded, contention, min_samples=10)
    monkeypatch.setattr("raidar.cli.perf.load_job_samples", lambda _: loaded)
    monkeypatch.setattr("raidar.cli.perf.build_report", lambda *a, **kw: report)
    result = CliRunner().invoke(main, ["perf", "check", "--strict", "--json", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["warnings"] == []
    assert payload["contention"]["overlapping_jobs"] == ["busy"]
    assert payload["contention"]["unknown_target_jobs"] == ["unknown"]
    assert payload["contention"]["untimed_trials"] == 1
    assert (
        next(c for c in payload["checks"] if c["phase"] == "step_archive_gap")["verdict"]
        == "not_applicable"
    )
    text = CliRunner().invoke(main, ["perf", "check", str(tmp_path)])
    assert text.exit_code == 0
    assert "r=unrecorded" in text.output
    assert "overlapping run: busy" in text.output
    assert "unknown shared target (not counted): unknown" in text.output
    assert "trials without start/finish timestamps: 1" in text.output
    assert "max=" in text.output
    assert "WARNING" not in text.output


@pytest.fixture
def memory_files(monkeypatch):
    """A reader boundary, not generated job directories on disk."""
    files: dict[Path, str] = {}
    monkeypatch.setattr(Path, "is_file", lambda path: path in files)
    monkeypatch.setattr(Path, "is_dir", lambda path: any(path in p.parents for p in files))
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: files[path])
    monkeypatch.setattr(
        Path,
        "glob",
        lambda path, pattern: iter(
            sorted(
                p
                for p in {*files, *(parent for p in files for parent in p.parents)}
                if p != path and p.is_relative_to(path) and p.relative_to(path).match(pattern)
            )
        ),
    )
    return files


def _memory_job(files: dict[Path, str], job: Path, offsets: list[float]) -> None:
    hashes = TrialHashes(
        lock="l",
        problem="p",
        arm="a",
        workflow="w",
        packages="s",
        primitive_code="s",
        agent="g",
        protocol="r",
        trial="t",
    )
    for index, offset in enumerate(offsets):
        result, _ = _timeline()
        origin = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=offset)
        result.update(
            started_at=origin.isoformat(), finished_at=(origin + timedelta(seconds=54)).isoformat()
        )
        record = TrialRecord(
            experiment="e",
            job_id=job.name,
            trial=PlannedTrial(
                trial_id=f"t{index}",
                attempt=index,
                order=0,
                sequence=index,
                arm="baseline",
                problem="p",
                problem_index=0,
                steps=("checkpoint_1", "checkpoint_2"),
                hashes=hashes,
            ),
            trial_dir=f"trials/t{index}",
            progression=ProgressionRule(mode="continue", reward_key="strict_pass_rate"),
            step_results=(),
            reached=(),
            reached_source="derived",
            validity=ExecutionValidity(checks=()),
            phases=(*_phases(), PhaseSample(phase="raidar_result_write", seconds=0.001)),
        )
        files[job / "trial-records" / f"t{index}.json"] = record.model_dump_json()
        files[job / "trials" / f"t{index}" / "result.json"] = json.dumps(result)
    files[job / "job.json"] = json.dumps(
        {
            "kind": "raidar-job",
            "concurrency": 1,
            "execution_target": {"name": "test-host", "docker_context": "test-docker"},
        }
    )


@pytest.mark.parametrize("sibling_target", ["same", "foreign", "unknown"])
def test_sibling_contention_counts_only_known_shared_targets(
    memory_files, sibling_target: str
) -> None:
    job = Path("/memory/jobs/e/measured")
    sibling = job.with_name("busy")
    later = job.parent.parent / "other" / "later"
    _memory_job(memory_files, job, [i * 100 for i in range(10)])
    _memory_job(memory_files, sibling, [150, 160])
    _memory_job(memory_files, later, [5000])
    manifest = json.loads(memory_files[sibling / "job.json"])
    if sibling_target != "same":
        manifest["execution_target"] = (
            {"name": "remote-host"} if sibling_target == "foreign" else None
        )
    memory_files[sibling / "job.json"] = json.dumps(manifest)
    report = build_report([job], load_job_samples([job]), min_samples=10)
    contention = report.contention
    assert contention.configured_concurrency == {str(job): 1}
    assert contention.peak_concurrent_trials == 1
    assert contention.peak_including_other_jobs == (3 if sibling_target == "same" else 1)
    assert contention.overlapping_jobs == ((str(sibling),) if sibling_target == "same" else ())
    assert contention.unknown_target_jobs == (
        (str(sibling),) if sibling_target == "unknown" else ()
    )


def test_untimed_trials_and_unrecorded_concurrency_remain_explicit(memory_files) -> None:
    job = Path("/memory/jobs/e/measured")
    _memory_job(memory_files, job, [0, 100])
    manifest = json.loads(memory_files[job / "job.json"])
    manifest["concurrency"] = True
    memory_files[job / "job.json"] = json.dumps(manifest)
    result_path = job / "trials/t0/result.json"
    result = json.loads(memory_files[result_path])
    result.pop("finished_at")
    memory_files[result_path] = json.dumps(result)
    report = build_report([job], load_job_samples([job]), min_samples=1)
    assert report.contention.untimed_trials == 1
    assert report.contention.configured_concurrency == {str(job): None}
    memory_files[result_path] = json.dumps(
        {"started_at": result["started_at"], "finished_at": "2020-01-01T00:00:00+00:00"}
    )
    assert (
        build_report([job], load_job_samples([job]), min_samples=1).contention.untimed_trials == 1
    )
    memory_files[job / "trials/t1/result.json"] = "{}"
    untimed = build_report([job], load_job_samples([job]), min_samples=1).contention
    assert untimed.untimed_trials == 2
    assert untimed.peak_concurrent_trials == 0
    assert untimed.overlapping_jobs == ()


@pytest.mark.parametrize("broken", ["record", "manifest", "result", "manifest-shape"])
def test_malformed_records_are_input_errors_not_budget_warnings(memory_files, broken: str) -> None:
    job = Path("/memory/jobs/e/measured")
    _memory_job(memory_files, job, [0])
    path = {
        "record": job / "trial-records/t0.json",
        "result": job / "trials/t0/result.json",
        "manifest": job / "job.json",
        "manifest-shape": job / "job.json",
    }[broken]
    memory_files[path] = "[]" if broken == "manifest-shape" else "{invalid"
    with pytest.raises(PerfInputError, match=str(path)):
        build_report([job], load_job_samples([job]), min_samples=1)


def test_mixed_target_jobs_are_refused_before_budgets_are_combined(memory_files) -> None:
    job = Path("/memory/jobs/e/measured")
    other = job.with_name("other")
    _memory_job(memory_files, job, [0])
    _memory_job(memory_files, other, [0])
    for manifest in ({}, {"execution_target": {"name": "another"}}):
        memory_files[other / "job.json"] = json.dumps(manifest)
        with pytest.raises(PerfInputError, match="different execution targets"):
            load_job_samples([job, other])
    memory_files[other / "job.json"] = memory_files[job / "job.json"]
    assert load_job_samples([job, other]).trial_count == 2
    del memory_files[other / "job.json"]
    del memory_files[job / "job.json"]
    loaded = load_job_samples([job, other])
    assert loaded.target is None
    assert loaded.budget_set.label == "N1 reference"


def test_phase_reader_uses_recorded_values_and_omits_missing_or_malformed_measurements(
    memory_files,
) -> None:
    trial = Path("/memory/trial")
    result, _ = _timeline(1)
    memory_files[trial / "result.json"] = json.dumps(result)
    record = _record("checkpoint_1", StepOutcome("checkpoint_1"), trial)
    directory = step_record_dir(trial, "checkpoint_1")
    memory_files[directory / RECORD_FILE] = record.model_dump_json()
    memory_files[directory / PRE_AGENT_LEAK_FILE] = record.pre_agent_leak_check.model_dump_json()
    reset = trial / "steps/checkpoint_1/agent/raidar-reset.json"
    memory_files[reset] = json.dumps({"duration_ms": 240})
    samples = trial_phases(trial, arm_apply_s=0.25, environment_cold=False, compose_init=True)
    phases = {s.phase: s for s in samples}
    assert phases["per_step_reset"].seconds == 1.5
    assert phases["snapshot_transfer"].condition == "artifact"
    assert phases["grader_image"].condition == "warm"
    assert phases["setup_sh"].seconds == 0.24
    for malformed in ("{invalid", "[]", '{"duration_ms": "unknown"}'):
        memory_files[reset] = malformed
        assert "setup_sh" not in {
            s.phase
            for s in trial_phases(trial, arm_apply_s=0, environment_cold=True, compose_init=False)
        }
    del memory_files[reset]
    del memory_files[directory / RECORD_FILE]
    del memory_files[directory / PRE_AGENT_LEAK_FILE]
    absent = trial_phases(trial, arm_apply_s=0, environment_cold=False, compose_init=True)
    assert "setup_sh" not in {s.phase for s in absent}
    assert "snapshot_transfer" not in {s.phase for s in absent}
    assert next(s for s in absent if s.phase == "per_step_reset").seconds == 2.0
    assert trial_phases(
        Path("/memory/missing"), arm_apply_s=0, environment_cold=False, compose_init=True
    ) == [PhaseSample(phase="arm_apply", seconds=0)]
