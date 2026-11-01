"""Missing declared primitive evidence must not make a judged arm look cheaper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.framework.trial_inputs import ran_judge
from raidar.scoring.resource_usage.scorer import Measured, checkpoint_values


def _cost(trace: Path, workflow: dict[str, object], retained: bool = True) -> Measured:
    if retained:
        (trace / "workflow").mkdir()
        (trace / "workflow/workflow.json").write_text(json.dumps(workflow))
    step = StepInputs(
        step="checkpoint_1",
        reached=True,
        checkpoint="checkpoint_1",
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace,
        trace_digest=None,
        agent_result={"cost_usd": 1.0},
        judged=ran_judge({"metadata": {"workflow": workflow}}),
    )
    return checkpoint_values(step, Measured(None, AssessStatus.UNAVAILABLE))["cost_usd"]


@pytest.mark.parametrize("evidence", [None, [], {}, {"judge_calls": None}])
def test_declared_but_unreadable_judge_is_not_zero_spend(tmp_path: Path, evidence: object) -> None:
    measured = _cost(tmp_path, {"jev-judge/1": evidence})
    assert measured.status is AssessStatus.UNAVAILABLE
    assert measured.value is None


def test_no_declared_judge_costs_only_the_agent(tmp_path: Path) -> None:
    measured = _cost(tmp_path, {"frame/1": {}, "outputs/1": {}})
    assert measured.status is AssessStatus.OK
    assert measured.value == 1.0


def test_judge_declaration_survives_a_missing_trace_record(tmp_path: Path) -> None:
    measured = _cost(tmp_path, {"jev-judge/1": {"judge_calls": []}}, retained=False)
    assert measured.status is AssessStatus.UNAVAILABLE
    assert measured.value is None
