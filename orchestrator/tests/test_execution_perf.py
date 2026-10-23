"""N1 phase recording and ``raidar perf check`` against the accepted budgets (D4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.execution.budgets import (
    N1_REFERENCE_BUDGETS,
    TARGET_BUDGETS,
    Budget,
    Verdict,
    check_budgets,
    checks_pass,
    percentile,
)
from raidar.execution.models import (
    ExecutionValidity,
    PhaseSample,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
)
from raidar.execution.phases import trial_phases
from tests.execution_support import StepOutcome, write_trial


def _by_phase(samples: list[PhaseSample]) -> dict[tuple[str, str | None], PhaseSample]:
    return {(s.phase, s.step): s for s in samples}


def test_every_n1_phase_is_recorded_with_phase0_definitions(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "t", [StepOutcome("checkpoint_1"), StepOutcome("checkpoint_2", passed=False)]
    )
    samples = trial_phases(trial, component_apply_s=0.25, environment_cold=False, compose_init=True)
    phases = _by_phase(samples)
    assert phases[("environment_start", None)].seconds == 10.0
    assert phases[("environment_start", None)].condition == "warm"
    assert phases[("component_apply", None)].seconds == 0.25
    # 2 s gap before the agent, of which 0.5 s is the pre-agent leak scan.
    assert phases[("per_step_reset", "checkpoint_1")].seconds == pytest.approx(1.5)
    assert phases[("pre_agent_leak_check", "checkpoint_1")].seconds == 0.5
    assert phases[("setup_sh", "checkpoint_1")].seconds == 0.24
    assert phases[("artifact_collection", "checkpoint_1")].seconds == 3.0
    assert phases[("snapshot_transfer", "checkpoint_1")].condition == "artifact"
    assert phases[("verifier_plumbing", "checkpoint_2")].seconds == 0.2
    assert phases[("step_archive_gap", "checkpoint_1")].seconds == pytest.approx(1.5)
    assert ("step_archive_gap", "checkpoint_2") not in phases
    assert phases[("teardown_result_write", None)].condition == "init"
    per_step = phases[("per_step_overhead", "checkpoint_1")].seconds
    assert per_step == pytest.approx(1.5 + 3.0 + 0.01 + 0.2 + 1.5)
    fixed = phases[("per_trial_fixed_overhead", None)]
    assert fixed.seconds == pytest.approx(10.0 + 0.25 + 3.0)
    assert fixed.condition == "init"
    cold = trial_phases(trial, component_apply_s=0.1, environment_cold=True, compose_init=False)
    cold_phases = _by_phase(cold)
    assert cold_phases[("environment_start", None)].condition == "cold"
    assert cold_phases[("per_trial_fixed_overhead", None)].condition == "cold"


def _samples(phase: str, values: list[float], condition: str | None = None) -> list[PhaseSample]:
    return [PhaseSample(phase=phase, seconds=v, condition=condition) for v in values]


def test_p95_over_enough_samples_decides_the_verdict() -> None:
    budget = (Budget("per_step_reset", 2.0),)
    under = check_budgets(_samples("per_step_reset", [1.0] * 9 + [2.5]), budgets=budget)
    assert percentile([1.0] * 9 + [2.5], 0.95) == pytest.approx(1.825)
    assert under[0].verdict is Verdict.PASS
    over = check_budgets(_samples("per_step_reset", [1.0] * 8 + [2.5, 2.5]), budgets=budget)
    assert over[0].verdict is Verdict.FAIL
    few = check_budgets(_samples("per_step_reset", [1.0] * 9), budgets=budget)
    assert few[0].verdict is Verdict.INSUFFICIENT
    assert not checks_pass(few)
    lowered = check_budgets(_samples("per_step_reset", [1.0] * 9), budgets=budget, min_samples=5)
    assert checks_pass(lowered)


def test_conditions_select_budgets_and_cold_is_a_tripwire() -> None:
    samples = (
        _samples("snapshot_transfer", [1.5] * 10, "download")
        + _samples("environment_start", [300.0], "cold")
        + _samples("teardown_result_write", [10.0] * 10, "no-init")
    )
    checks = {
        (c.budget.phase, c.budget.condition): c
        for c in check_budgets(samples, budgets=N1_REFERENCE_BUDGETS)
    }
    assert checks[("snapshot_transfer", "download")].verdict is Verdict.PASS
    assert ("snapshot_transfer", "artifact") not in checks
    assert checks[("environment_start", "cold")].verdict is Verdict.PASS
    assert checks[("teardown_result_write", "no-init")].verdict is Verdict.PASS
    assert checks[("per_step_reset", None)].verdict is Verdict.MISSING
    tripped = check_budgets(_samples("environment_start", [500.0], "cold"))
    assert next(c for c in tripped if c.budget.phase == "environment_start").verdict is Verdict.FAIL


def test_between_step_budgets_apply_only_when_a_trial_ran_two_checkpoints() -> None:
    budgets = (Budget("per_step_reset", 2.0), Budget("step_archive_gap", 2.5, multi_step_only=True))
    samples = _samples("per_step_reset", [1.0] * 10)
    one_shot = check_budgets(samples, budgets=budgets, multi_step=False)
    assert [c.verdict for c in one_shot] == [Verdict.PASS, Verdict.NOT_APPLICABLE]
    assert checks_pass(one_shot)
    multi = check_budgets(samples, budgets=budgets, multi_step=True)
    assert multi[1].verdict is Verdict.MISSING
    assert not checks_pass(multi)


def _job_dir(root: Path, trials: int, steps: int = 2) -> Path:
    hashes = TrialHashes(
        lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
    )
    names = tuple(f"checkpoint_{k}" for k in range(1, steps + 1))
    for index in range(trials):
        trial_dir = write_trial(
            root / "trials" / f"t{index}", [StepOutcome(name) for name in names]
        )
        phases = trial_phases(
            trial_dir, component_apply_s=0.05, environment_cold=False, compose_init=True
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


def test_perf_check_command_passes_and_fails_on_recorded_runs(tmp_path: Path) -> None:
    runner = CliRunner()
    run = _job_dir(tmp_path / "run", 10)
    # The synthetic timeline has 10 s environment starts and 3 s collection: within budget,
    # except the 3 s teardown, which is also within the 4 s init budget.
    result = runner.invoke(main, ["perf", "check", str(run), "--json"])
    payload = json.loads(result.output)
    verdicts = {(c["phase"], c["condition"]): c["verdict"] for c in payload["checks"]}
    assert result.exit_code == 0, verdicts
    assert verdicts[("per_step_overhead", None)] == "pass"
    assert "scorer_orchestration" in payload["not_recorded_by_runs"]
    short_run = str(_job_dir(tmp_path / "short", 3))
    # Report-only by default: an insufficient phase is a warning, not a failure ...
    short = runner.invoke(main, ["perf", "check", short_run])
    assert short.exit_code == 0, short.output
    assert "WARNING insufficient" in short.output
    assert "report-only" in short.output
    # ... and --strict restores the failing gate.
    strict = runner.invoke(main, ["perf", "check", "--strict", short_run])
    assert strict.exit_code == 1
    assert "insufficient" in strict.output
    # A one-shot (K=1) experiment has no between-step gap and still passes.
    one_shot = runner.invoke(main, ["perf", "check", str(_job_dir(tmp_path / "k1", 10, 1))])
    assert one_shot.exit_code == 0, one_shot.output
    assert "not_applicable" in one_shot.output
    empty = tmp_path / "empty"
    empty.mkdir()
    assert runner.invoke(main, ["perf", "check", str(empty)]).exit_code != 0


def _recorded(job: Path, target: str | None) -> Path:
    manifest = {} if target is None else {"execution_target": {"name": target}}
    (job / "job.json").write_text(json.dumps(manifest))
    return job


def _check(*jobs: Path) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["perf", "check", "--strict", "--json", *map(str, jobs)])
    return result.exit_code, result.output


def test_budgets_follow_the_target_the_job_recorded(tmp_path: Path) -> None:
    # The synthetic /app collection takes 3.0 s: inside N1's 3.5 s, over mbp's 2.5 s.
    mbp = _recorded(_job_dir(tmp_path / "mbp", 10), "mbp")
    exit_code, output = _check(mbp)
    payload = json.loads(output)
    verdicts = {(c["phase"], c["condition"]): c["verdict"] for c in payload["checks"]}
    assert (exit_code, payload["budgets"]) == (1, "mbp (accepted 2026-10-24)")
    assert verdicts[("artifact_collection", None)] == "fail"
    for target in (None, "no-such-target"):  # unrecorded or unaccepted: the N1 reference
        job = _recorded(_job_dir(tmp_path / f"n1-{target}", 10), target)
        exit_code, output = _check(job)
        assert (exit_code, json.loads(output)["budgets"]) == (0, "N1 reference"), output
    assert "budgets: mbp (accepted" in CliRunner().invoke(main, ["perf", "check", str(mbp)]).output


def test_mbp_set_changes_only_the_phases_it_accepts() -> None:
    reference = {(b.phase, b.condition): b for b in N1_REFERENCE_BUDGETS}
    mbp = {(b.phase, b.condition): b for b in TARGET_BUDGETS["mbp"].budgets}
    assert mbp.keys() == reference.keys()
    assert mbp[("per_step_overhead", None)].seconds == 6.5
    assert mbp[("per_trial_fixed_overhead", "init")].seconds == 6.5
    assert mbp[("per_step_reset", None)].seconds == 2.5
    for key in (("per_trial_fixed_overhead", "no-init"), ("environment_start", "cold")):
        assert mbp[key] == reference[key]


def test_jobs_from_different_targets_are_not_checked_together(tmp_path: Path) -> None:
    mbp = _recorded(_job_dir(tmp_path / "mbp", 10), "mbp")
    other = _recorded(_job_dir(tmp_path / "other", 10), "laptop")
    unrecorded = _recorded(_job_dir(tmp_path / "old", 10), None)
    for pair in ((mbp, other), (mbp, unrecorded)):
        exit_code, output = _check(*pair)
        assert exit_code != 0
        assert "different execution targets" in output
    assert _check(mbp, _recorded(_job_dir(tmp_path / "mbp2", 10), "mbp"))[0] == 1
