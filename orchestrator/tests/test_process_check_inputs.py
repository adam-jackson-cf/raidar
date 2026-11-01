"""process-compliance: what a step's inputs allow it to measure, and the evidence it cites.

A step is measured only when it was reached, its snapshot is valid evidence and its ATIF
trajectory can be read; otherwise it carries a statused gap, never a value. Evidence cites
the deciding tool call and a plan file only when it exists with content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from raidar.scoring.framework.atif import Trajectory
from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.process_checks.assess import StepAssessment, measurable, step_evidence
from raidar.scoring.process_checks.checks import CheckOutcome, Verdict
from raidar.scoring.process_checks.outputs import call_outputs, not_run_total
from raidar.scoring.process_checks.trace_events import EventKind, TraceEvent
from raidar.scoring.process_checks.work_session import work_events
from tests.trace_trial_support import agent_step, session, trajectory

METRICS = ("tests_run_before_finish",)


def _inputs(
    *, reached: bool = True, snapshot: Path | None = None, trace_dir: Path | None = None
) -> StepInputs:
    return StepInputs(
        step="checkpoint_1",
        reached=reached,
        checkpoint="checkpoint_1",
        snapshot=snapshot,
        snapshot_digest=None,
        record=None,
        record_digest=None,
        trace_dir=trace_dir,
        trace_digest=None,
        agent_result=None,
    )


def _measure(inputs: StepInputs) -> Trajectory | StepAssessment:
    return measurable(AssessContext(problem="toy", steps=(inputs,)), inputs, METRICS)


def _gap(outcome: Trajectory | StepAssessment) -> Observation:
    assert isinstance(outcome, StepAssessment)
    (observation,) = outcome.observations
    return observation


def test_an_unreached_step_is_not_applicable() -> None:
    gap = _gap(_measure(_inputs(reached=False)))

    assert (gap.status, gap.reason) == (AssessStatus.NOT_APPLICABLE, "checkpoint not reached")


def test_a_snapshot_with_invalid_evidence_makes_the_step_ineligible(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe.py")

    gap = _gap(_measure(_inputs(snapshot=tmp_path)))

    assert gap.status is AssessStatus.INELIGIBLE
    assert gap.reason == "invalid snapshot: snapshot entry is not a regular file"


def test_a_malformed_trajectory_is_unavailable_with_the_reason(tmp_path: Path) -> None:
    (tmp_path / "trajectory.json").write_text("{not json")

    outcome = _measure(_inputs(trace_dir=tmp_path))

    gap = _gap(outcome)
    assert gap.status is AssessStatus.UNAVAILABLE
    assert (gap.reason or "").startswith("trajectory unreadable: ")
    assert isinstance(outcome, StepAssessment)
    assert outcome.unreadable


def test_a_trace_directory_without_a_trajectory_is_unavailable(tmp_path: Path) -> None:
    gap = _gap(_measure(_inputs(trace_dir=tmp_path)))

    assert (gap.status, gap.reason) == (
        AssessStatus.UNAVAILABLE,
        "the step has no ATIF trajectory",
    )


def test_a_readable_trajectory_is_measured(tmp_path: Path) -> None:
    (tmp_path / "trajectory.json").write_text(json.dumps(trajectory(agent_step(1))))

    assert isinstance(_measure(_inputs(trace_dir=tmp_path)), Trajectory)


def _event(kind: EventKind, call_id: str | None, path: str | None, session_id: str = "main"):
    return TraceEvent(0, kind, session_id, "write", call_id, f"{kind.value} {path}", path)


def test_evidence_cites_the_deciding_call_and_only_a_plan_file_with_content(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "PLAN.md").write_text("1. do it\n")
    (snapshot / "EMPTY.md").write_text("")
    trace = tmp_path / "trajectory.json"
    trace.write_text('{\n  "tool_call_id": "hit",\n  "other": 1\n}\n')
    document = Trajectory(trace, ())
    events = (
        _event(EventKind.PLAN, "hit", "PLAN.md"),
        _event(EventKind.PLAN, "absent", "EMPTY.md"),
        _event(EventKind.PLAN, None, "missing.md"),
        _event(EventKind.PLAN, None, None),
    )
    passed = CheckOutcome("plan_written", Verdict.PASS, "planned", events)
    failed = CheckOutcome("tests_run_before_finish", Verdict.FAIL, "no", events)
    unsure = CheckOutcome("other", Verdict.AMBIGUOUS, "?", events)

    evidence = step_evidence(_inputs(snapshot=snapshot), (passed, failed, unsure), document)

    assert [(item.metric, item.kind) for item in evidence] == [
        ("plan_written", "trace-plan"),
        ("plan_written", "plan-file"),
        ("tests_run_before_finish", "trace-plan"),
    ]
    assert evidence[0].spans[0].line == 2
    assert evidence[1].path == "PLAN.md"


def test_a_passing_check_without_a_snapshot_cites_no_plan_file(tmp_path: Path) -> None:
    trace = tmp_path / "trajectory.json"
    trace.write_text("{}\n")
    event = _event(EventKind.PLAN, None, "PLAN.md")
    passed = CheckOutcome("plan_written", Verdict.PASS, "planned", (event,))

    assert step_evidence(_inputs(), (passed,), Trajectory(trace, ())) == ()


def test_the_work_session_is_every_session_when_none_received_a_prompt() -> None:
    events = (_event(EventKind.TEST, "a", None, "s1"), _event(EventKind.EDIT, "b", "x", "s2"))

    assert work_events(events, None) == events
    assert work_events(events, "s2") == (events[1],)


def _ran(call_id: str, *contents: object) -> dict:
    """An agent step calling a tool once, with one recorded result per content."""
    call = {"tool_call_id": call_id, "function_name": "x", "arguments": {}}
    results = [{"source_call_id": call_id, "content": content} for content in contents]
    return agent_step(1, call) | {"observation": {"results": results}}


def test_call_outputs_join_results_and_text_parts_across_every_session(tmp_path: Path) -> None:
    parts = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    root = trajectory(
        _ran("r1", "out", " more"),
        _ran("r2", None),
        agent_step(3) | {"observation": {"results": [{"content": "unlinked"}]}},
        subagents=(session("sub", _ran("s1", parts)),),
    )
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps(root))

    assert call_outputs(path) == {"r1": "out more", "r2": "", "s1": "a\nb"}


@pytest.mark.parametrize("text", ["{not json", '{"steps": 3}'])
def test_call_outputs_of_an_unreadable_trajectory_are_empty(tmp_path: Path, text: str) -> None:
    path = tmp_path / "trajectory.json"
    path.write_text(text)

    assert call_outputs(path) == {}
    assert call_outputs(tmp_path / "missing.json") == {}


def test_the_not_run_total_is_not_applicable_when_no_step_was_counted() -> None:
    unreached = Observation(
        metric="test_commands_not_run",
        subject="checkpoint_1",
        status=AssessStatus.NOT_APPLICABLE,
        reason="checkpoint not reached",
    )

    total = not_run_total((unreached,))

    assert total.subject == TRAJECTORY_SUBJECT
    assert total.status is AssessStatus.NOT_APPLICABLE
