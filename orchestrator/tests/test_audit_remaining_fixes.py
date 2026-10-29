"""Audit fixes #8, #11 and #12: the solvable halt reward, judge spend in cost, H5's setup limit."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from raidar.design.experiment.schema import ProtocolSpec
from raidar.engine.health.audit import audit_trial
from raidar.engine.job.provenance import effective_limits
from raidar.grading.solvable import solvable_pass_rate
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.resource_usage.scorer import Measured, checkpoint_values
from tests.execution_support import StepOutcome, solvable_cases, verifier_grade
from tests.health_support import healthy_trial

NO_TRAJECTORY = Measured(None, AssessStatus.UNAVAILABLE, "no transcript")


def test_the_solvable_reward_is_the_share_of_the_pinned_cases_that_passed() -> None:
    outcome = StepOutcome("checkpoint_1", passed=False)
    grade = verifier_grade("checkpoint_1", outcome)
    assert solvable_pass_rate(grade, solvable_cases("checkpoint_1", outcome)) == 0.5
    forgiven = StepOutcome("checkpoint_1", passed=False, forgiven=True)
    assert solvable_pass_rate(grade, solvable_cases("checkpoint_1", forgiven)) == 1.0
    assert solvable_pass_rate(grade, ()) is None  # no solvable set: no verdict, never zero


@pytest.mark.parametrize("setup_override", [None, 120.0])
def test_recorded_setup_limits_make_h5_measured_and_enforced(
    tmp_path: Path, setup_override: float | None
) -> None:
    config = SimpleNamespace(override_timeout_sec=900, override_setup_timeout_sec=setup_override)
    plan = SimpleNamespace(
        arms={"baseline": SimpleNamespace(agent_config=config)},
        loaded=SimpleNamespace(spec=SimpleNamespace(protocol=ProtocolSpec())),
    )
    trial = healthy_trial(tmp_path)
    trial.set_job(effective_limits=effective_limits(plan))  # type: ignore[arg-type]
    report = audit_trial(trial.job_dir, trial.trial_dir, trial.record, trial.job())
    assert next(check for check in report.checks if check.id == "H5").status == "pass"
    trial.patch_result(
        lambda result: result["agent_setup"].update(finished_at="2026-09-30T14:00:00+00:00")
    )
    report = audit_trial(trial.job_dir, trial.trial_dir, trial.record, trial.job())
    assert next(check for check in report.checks if check.id == "H5").status == "violation"


def _step(tmp_path: Path, calls: list[dict[str, object]] | None) -> StepInputs:
    if calls is not None:
        (tmp_path / "workflow").mkdir(parents=True)
        (tmp_path / "workflow" / "workflow.json").write_text(
            json.dumps({"verify": {"judge_calls": calls}})
        )
    return StepInputs(
        step="checkpoint_1",
        reached=True,
        checkpoint="checkpoint_1",
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=tmp_path,
        trace_digest=None,
        agent_result={"n_input_tokens": 1, "n_output_tokens": 1, "cost_usd": 1.0},
    )


def _call(cost: object, ok: bool = True) -> dict[str, object]:
    return {
        "request_file": None,
        "response_file": None,
        "result": {"ok": ok, "cost_usd": cost},
    }


def test_cost_counts_the_judge_spend_of_the_step(tmp_path: Path) -> None:
    step = _step(tmp_path, [_call(0.25), _call(0.5), _call(None, ok=False)])
    assert checkpoint_values(step, NO_TRAJECTORY)["cost_usd"].value == 1.75


def test_a_judge_call_without_a_cost_makes_the_cost_unavailable(tmp_path: Path) -> None:
    step = _step(tmp_path, [_call(None)])
    assert checkpoint_values(step, NO_TRAJECTORY)["cost_usd"].value is None


def test_an_unreadable_workflow_record_cannot_show_that_no_judge_ran(tmp_path: Path) -> None:
    (tmp_path / "workflow").mkdir()
    (tmp_path / "workflow" / "workflow.json").write_text("{not json")
    step = _step(tmp_path, None)
    assert checkpoint_values(step, NO_TRAJECTORY)["cost_usd"].value is None


def test_a_step_with_no_judge_costs_what_the_agent_cost(tmp_path: Path) -> None:
    assert checkpoint_values(_step(tmp_path, None), NO_TRAJECTORY)["cost_usd"].value == 1.0


def test_a_charged_judge_failure_is_counted_and_an_unknown_charge_is_not_zero(
    tmp_path: Path,
) -> None:
    step = _step(tmp_path / "charged", [_call(0.25, ok=False)])
    assert checkpoint_values(step, NO_TRAJECTORY)["cost_usd"].value == 1.25
    sent = {**_call(None, ok=False), "request_file": "request.json"}
    unknown = _step(tmp_path / "unknown", [sent])
    assert checkpoint_values(unknown, NO_TRAJECTORY)["cost_usd"].value is None


def test_a_judged_step_with_missing_workflow_evidence_does_not_claim_zero_spend(
    tmp_path: Path,
) -> None:
    step = replace(_step(tmp_path, None), judged=True)
    assert checkpoint_values(step, NO_TRAJECTORY)["cost_usd"].value is None
