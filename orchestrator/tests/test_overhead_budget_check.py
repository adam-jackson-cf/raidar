"""Slow seams: Harbor records become phase timings and public perf reports."""

from __future__ import annotations

import json
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
from raidar.engine.perf.phases import trial_phases
from raidar.engine.trial.workspace import seed_duration
from tests.execution_support import StepOutcome, write_seed_record, write_trial

pytestmark = pytest.mark.slow


def test_trial_records_supply_phase_and_workspace_seed_measurements(tmp_path: Path) -> None:
    trial = write_trial(tmp_path / "trial", [StepOutcome("checkpoint_1")])
    assert seed_duration(trial, ("checkpoint_1",)) is None
    write_seed_record(trial, "checkpoint_1", duration_s=4.0)
    samples = trial_phases(
        trial,
        arm_apply_s=0.25,
        environment_cold=True,
        compose_init=True,
        workspace_seed_s=seed_duration(trial, ("checkpoint_1",)),
    )
    by_phase = {s.phase: s for s in samples}
    assert by_phase["workspace_seed"].seconds == 4.0
    assert by_phase["environment_start"].seconds == 6.0
    assert by_phase["setup_sh"].seconds == 0.24
    assert by_phase["snapshot_transfer"].condition == "artifact"


def _job_dir(root: Path, trials: int, steps: int = 2) -> Path:
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
    names = tuple(f"checkpoint_{k}" for k in range(1, steps + 1))
    for index in range(trials):
        trial_dir = write_trial(
            root / "trials" / f"t{index}", [StepOutcome(name) for name in names]
        )
        phases = trial_phases(
            trial_dir, arm_apply_s=0.05, environment_cold=False, compose_init=True
        )
        record = TrialRecord(
            experiment="e",
            job_id="r",
            trial=PlannedTrial(
                trial_id=f"t{index}",
                attempt=index,
                order=0,
                sequence=index,
                arm="baseline",
                problem="p",
                problem_index=0,
                steps=names,
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
        path = root / "trial-records" / f"t{index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json())
    return root


def test_perf_cli_reads_records_and_refuses_malformed_input(tmp_path: Path) -> None:
    run = _job_dir(tmp_path / "jobs" / "experiment" / "job", 10)
    manifest = run / "job.json"
    manifest.write_text(json.dumps({"concurrency": 1, "execution_target": {"name": "local"}}))
    runner = CliRunner()
    checked = runner.invoke(main, ["perf", "check", "--strict", "--json", str(run)])
    assert checked.exit_code == 0, checked.output
    payload = json.loads(checked.output)
    assert payload["passed"] is True
    assert payload["trials"] == 10
    assert payload["contention"]["configured_concurrency"] == {str(run): 1}
    assert "scorer_orchestration" in payload["not_recorded_by_runs"]
    for path in (
        run / "trial-records" / "t0.json",
        run / "trials" / "t0" / "result.json",
        manifest,
    ):
        original = path.read_bytes()
        path.write_text("{not json")
        refused = runner.invoke(main, ["perf", "check", str(run)])
        assert refused.exit_code != 0
        assert str(path) in " ".join(refused.output.split())
        path.write_bytes(original)
