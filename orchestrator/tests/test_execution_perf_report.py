"""``raidar perf check`` on real runs: report-only exits, --strict, contention context,
and the ``make perf-gate`` fixture wiring (user decision 2026-10-01 on N1 budgets)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.cli.shared import REPO_ROOT
from raidar.execution.models import (
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
)
from raidar.execution.perf.contention import TrialWindow, peak_during, peak_overlap
from raidar.execution.perf.phases import trial_phases
from tests.execution_support import StepOutcome, write_trial
from tests.test_execution_perf_fixture import STRICT_CHECK

TEARDOWN = "teardown_result_write"


def _shift(trial_dir: Path, offset_s: float) -> None:
    """Move a trial's start/finish window by ``offset_s`` seconds."""
    path = trial_dir / "result.json"
    result = json.loads(path.read_text())
    for key in ("started_at", "finished_at"):
        moved = datetime.fromisoformat(result[key]) + timedelta(seconds=offset_s)
        result[key] = moved.isoformat()
    path.write_text(json.dumps(result))


def _record(job_dir: Path, index: int, teardown_s: float) -> TrialRecord:
    trial_dir = job_dir / "trials" / f"t{index}"
    phases = [
        sample if sample.phase != TEARDOWN else sample.model_copy(update={"seconds": teardown_s})
        for sample in trial_phases(
            trial_dir, component_apply_s=0.05, environment_cold=False, compose_init=True
        )
    ]
    hashes = TrialHashes(
        lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
    )
    return TrialRecord(
        experiment="e",
        job_id=job_dir.name,
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
        phases=(*phases, PhaseSample(phase="raidar_result_write", seconds=0.001)),
    )


def _run(
    job_dir: Path,
    offsets: list[float],
    *,
    teardown: Callable[[int], float] = lambda _: 1.0,
    concurrency: int | None = 1,
    trials: bool = True,
) -> Path:
    """A run whose trial ``i`` starts at ``offsets[i]`` s (each trial lasts 54 s)."""
    steps = [StepOutcome("checkpoint_1"), StepOutcome("checkpoint_2")]
    for index, offset in enumerate(offsets):
        _shift(write_trial(job_dir / "trials" / f"t{index}", steps), offset)
        if trials:
            path = job_dir / "trial-records" / f"t{index}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_record(job_dir, index, teardown(index)).model_dump_json())
    manifest = {"kind": "raidar-job", "job_id": job_dir.name, "concurrency": concurrency}
    (job_dir / "job.json").write_text(json.dumps(manifest))
    return job_dir


def _check(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(main, ["perf", "check", "--json", *args])
    return result.exit_code, json.loads(result.output) if result.exit_code in (0, 1) else {}


def _window(start: float, end: float, trial: str = "t") -> TrialWindow:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    return TrialWindow("r", trial, t0 + timedelta(seconds=start), t0 + timedelta(seconds=end))


def test_peak_overlap_counts_open_windows_and_touching_windows_do_not_overlap() -> None:
    windows = [_window(0, 10, "a"), _window(10, 20, "b"), _window(5, 15, "c"), _window(6, 7, "d")]
    assert peak_overlap([]) == 0
    assert peak_overlap(windows[:2]) == 1
    assert peak_overlap(windows) == 3
    assert peak_during(windows[1], windows) == 2
    assert peak_during(windows[0], windows) == 3


def test_breaches_are_report_only_warnings_and_strict_fails(tmp_path: Path) -> None:
    run = _run(tmp_path / "jobs" / "e" / "r1", [i * 100.0 for i in range(10)], teardown=lambda _: 6)
    code, payload = _check(str(run))
    assert code == 0
    assert payload["strict"] is False
    assert payload["passed"] is False
    warned = {(w["phase"], w["condition"]): w for w in payload["warnings"]}
    assert warned[(TEARDOWN, "init")]["verdict"] == "fail"
    assert warned[(TEARDOWN, "init")]["p95_s"] == pytest.approx(6.0)
    strict_code, strict_payload = _check("--strict", str(run))
    assert strict_code == 1
    assert strict_payload["strict"] is True
    text = CliRunner().invoke(main, ["perf", "check", str(run)])
    assert text.exit_code == 0
    assert f"WARNING fail         {TEARDOWN}[init]" in text.output
    assert "pass --strict to fail on them" in text.output


def test_contention_records_concurrency_and_splits_a_breach_by_it(tmp_path: Path) -> None:
    # Six sequential trials (1 open at a time), then four started together (4 open):
    # only the contended four have a slow teardown.
    offsets = [i * 100.0 for i in range(6)] + [1000.0] * 4
    run = _run(
        tmp_path / "jobs" / "e" / "r1",
        offsets,
        teardown=lambda index: 6.0 if index >= 6 else 1.0,
        concurrency=4,
    )
    code, payload = _check("--min-samples", "3", str(run))
    assert code == 0
    contention = payload["contention"]
    assert contention["configured_concurrency"] == {str(run): 4}
    assert contention["peak_concurrent_trials"] == 4
    assert contention["peak_including_other_jobs"] == 4
    assert contention["overlapping_jobs"] == []
    assert "not detected" in contention["other_docker_activity"]
    warning = next(w for w in payload["warnings"] if w["phase"] == TEARDOWN)
    split = {s["concurrent_trials"]: s for s in warning["by_concurrency"]}
    assert split["<=3"]["verdict"] == "pass"
    assert split["<=3"]["samples"] == 6
    assert split[">=4"]["verdict"] == "fail"
    assert split[">=4"]["p95_s"] == pytest.approx(6.0)
    text = CliRunner().invoke(main, ["perf", "check", "--min-samples", "3", str(run)])
    assert "peak concurrent trials (checked runs): 4" in text.output
    assert "at >=4 concurrent: fail" in text.output


def test_overlapping_sibling_runs_are_recorded_as_contention(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    run = _run(root / "e" / "r1", [i * 100.0 for i in range(10)])
    _run(root / "e" / "busy", [150.0, 160.0], trials=False)
    _run(root / "other" / "later", [5000.0], trials=False)
    code, payload = _check(str(run))
    assert code == 0, payload
    contention = payload["contention"]
    assert contention["overlapping_jobs"] == [str((root / "e" / "busy").resolve())]
    assert contention["peak_concurrent_trials"] == 1
    assert contention["peak_including_other_jobs"] == 3


def test_untimed_trials_and_unrecorded_concurrency_are_reported(tmp_path: Path) -> None:
    run = _run(tmp_path / "jobs" / "e" / "r1", [i * 100.0 for i in range(10)], concurrency=None)
    result = json.loads((run / "trials" / "t0" / "result.json").read_text())
    result.pop("finished_at")
    (run / "trials" / "t0" / "result.json").write_text(json.dumps(result))
    code, payload = _check(str(run))
    assert code == 0
    assert payload["contention"]["untimed_trials"] == 1
    assert payload["contention"]["configured_concurrency"] == {str(run): None}
    text = CliRunner().invoke(main, ["perf", "check", str(run)])
    assert "configured concurrency: " + f"{run}=unrecorded" in text.output
    assert "trials without start/finish timestamps: 1" in text.output


@pytest.mark.parametrize("broken", ["trajectory", "manifest", "result"])
def test_malformed_input_exits_non_zero_even_when_report_only(tmp_path: Path, broken: str) -> None:
    run = _run(tmp_path / "jobs" / "e" / "r1", [i * 100.0 for i in range(10)])
    target = {
        "trajectory": run / "trial-records" / "t0.json",
        "manifest": run / "job.json",
        "result": run / "trials" / "t0" / "result.json",
    }[broken]
    target.write_text("{not json")
    result = CliRunner().invoke(main, ["perf", "check", str(run)])
    assert result.exit_code != 0
    assert "Error" in result.output
    assert str(target) in " ".join(result.output.split())


def test_empty_run_exits_non_zero(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert CliRunner().invoke(main, ["perf", "check", str(empty)]).exit_code != 0


def _dry_run(*args: str) -> str:
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_perf_gate_runs_the_strict_fixture_at_concurrency_1_and_3(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    output = _dry_run("perf-gate", f"PERF_GATE_DIR={gate}")
    assert f'rm -rf "{gate}"' in output
    assert output.count("tests/test_execution_perf_fixture.py") == 2
    assert output.count("export RAIDAR_EXECUTION_DOCKER=1") == 2
    for concurrency in (1, 3):
        assert f'export RAIDAR_PERF_FIXTURE_CONCURRENCY="{concurrency}"' in output
        assert f'RAIDAR_PERF_FIXTURE_DIR="{gate}/concurrency-{concurrency}"' in output
    assert output.index('CONCURRENCY="1"') < output.index('CONCURRENCY="3"')
    # The fixture is the enforced gate: its budget check is strict.
    assert "--strict" in STRICT_CHECK


def test_perf_check_target_is_report_only_unless_perf_strict(tmp_path: Path) -> None:
    default = _dry_run("perf-check", f"JOB={tmp_path}")
    assert f"raidar perf check {tmp_path}" in default
    assert "--strict" not in default
    assert "--strict" in _dry_run("perf-check", f"JOB={tmp_path}", "PERF_STRICT=1")


def test_make_quality_does_not_run_the_docker_perf_gate() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nquality:\n", 1)[1].split("\n\n", 1)[0]
    assert "perf" not in recipe
