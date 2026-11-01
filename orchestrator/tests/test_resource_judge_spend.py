"""resource-usage: judge spend is summed from recorded calls, and unknown spend is never zero.

A malformed workflow record, a call without a result and a sent call with no billed cost leave
the step's cost unavailable; a call that was never sent costs nothing; totals skip steps that
were not reached and are not applicable when none was.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.resource_usage.scorer import Measured, checkpoint_values, total

UNSENT = {"result": {"ok": False}, "request_file": None, "response_file": None}


def _step(trace: Path, record: object, *, reached: bool = True) -> StepInputs:
    (trace / "workflow").mkdir(exist_ok=True)
    (trace / "workflow/workflow.json").write_text(json.dumps(record))
    return StepInputs(
        step="checkpoint_1",
        reached=reached,
        checkpoint="checkpoint_1",
        snapshot=None,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace,
        trace_digest=None,
        agent_result={"cost_usd": 1.0},
        judged=True,
    )


def _cost(trace: Path, record: object) -> Measured:
    unknown = Measured(None, AssessStatus.UNAVAILABLE)
    return checkpoint_values(_step(trace, record), unknown)["cost_usd"]


def _judge(*calls: object) -> dict[str, object]:
    return {"jev-judge/1": {"judge_calls": list(calls)}}


def test_a_workflow_record_that_is_not_an_object_leaves_the_cost_unavailable(
    tmp_path: Path,
) -> None:
    measured = _cost(tmp_path, ["not", "an", "object"])

    assert measured.status is AssessStatus.UNAVAILABLE
    assert measured.reason == "the workflow record is malformed: judge spend is unknown"


def test_a_judge_call_without_a_result_record_leaves_the_cost_unavailable(tmp_path: Path) -> None:
    measured = _cost(tmp_path, _judge({"request_file": "r.json"}, "stray"))

    assert measured.status is AssessStatus.UNAVAILABLE
    assert measured.reason == "a judge call has no result record"


def test_a_sent_call_with_no_billed_cost_leaves_the_cost_unavailable(tmp_path: Path) -> None:
    sent = {"result": {"ok": False}, "request_file": "q.json", "response_file": None}

    measured = _cost(tmp_path, _judge(sent))

    assert measured.status is AssessStatus.UNAVAILABLE
    assert measured.reason == "a judge call reported no cost"


def test_judge_spend_adds_billed_calls_and_an_unsent_request_costs_nothing(tmp_path: Path) -> None:
    billed = [{"result": {"ok": True, "cost_usd": cost}} for cost in (0.25, 0.5)]

    measured = _cost(tmp_path, _judge(*billed, UNSENT))

    assert measured.status is AssessStatus.OK
    assert measured.value == pytest.approx(1.75)


def test_a_total_over_steps_that_were_not_reached_is_not_applicable(tmp_path: Path) -> None:
    unreached = checkpoint_values(_step(tmp_path, {}, reached=False), Measured(None))

    measured = total(("cost_usd",), [unreached, unreached])

    assert measured.status is AssessStatus.NOT_APPLICABLE
    assert measured.reason == "no checkpoint was reached"
