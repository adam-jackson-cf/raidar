"""``stop/1``: strict results, bounded rounds, one repair, irreversible close and attribution."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive.calls import ResourceFloors
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.primitive.stop import StopState, run_stop
from tests.lifecycle_support import (
    WATCH,
    FakeContainer,
    Rig,
    ToolOutcome,
    build_rig,
    note_tool,
    stop_json,
)


def closed_flag(rig: Rig) -> str:
    return f"/var/lib/raidar-calls/closed/{rig.site.scope}-note-state"


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return build_rig(tmp_path, monkeypatch)


def broken_for_agent(outcome: ToolOutcome) -> Any:
    """A tool that fails on the agent's state while its fixture (canary) state still works."""

    def tool(container: FakeContainer, argv: Sequence[str], context: dict[str, Any]) -> ToolOutcome:
        if "/canary/" in context["state"]:
            return note_tool(container, argv, context)
        return outcome

    return tool


def always(outcome: ToolOutcome) -> Any:
    return lambda *_: outcome


def raw(document: dict[str, Any]) -> ToolOutcome:
    return ToolOutcome(json.dumps(document).encode())


def stop_calls(rig: Rig) -> list[dict[str, Any]]:
    return rig.container.tool_calls("stop/1")


async def test_a_done_result_with_its_outputs_is_the_agents_outcome_as_reported(rig: Rig) -> None:
    rig.container.write_note()

    decision = await run_stop(rig.site, StopState(), rig.canary)

    assert (decision.action, decision.reason) == ("done", "reported")
    copied = decision.record["outputs"]["note"]
    assert Path(copied["host_path"]).read_text() == "a short note of words"
    assert rig.canary.runs == []


async def test_a_resume_hands_its_message_and_carry_to_the_next_call_unchanged(rig: Rig) -> None:
    state = StopState()

    first = await run_stop(rig.site, state, rig.canary)
    rig.container.write_note()
    second = await run_stop(rig.site, state, rig.canary)

    assert (first.action, first.message) == ("resume", "Write a note of at least 3 words.")
    assert second.action == "done"
    assert [c["round"] for c in stop_calls(rig)] == [0, 1]
    assert stop_calls(rig)[0]["carry"] == {}
    assert stop_calls(rig)[1]["carry"] == {"words": 0}


async def test_resumes_stop_at_the_round_bound_and_the_close_is_irreversible(rig: Rig) -> None:
    rig.container.tool = always(stop_json("resume", "again"))
    state = StopState()

    actions = [(await run_stop(rig.site, state, rig.canary)).action for _ in range(3)]

    assert actions == ["resume", "resume", "closed"]
    assert state.closed
    assert state.rounds == 2
    assert closed_flag(rig) in rig.container.root_files
    rig.container.tool = always(stop_json("done"))
    rig.container.write_note()
    calls = len(stop_calls(rig))

    assert (await run_stop(rig.site, state, rig.canary)).action == "closed"
    assert len(stop_calls(rig)) == calls


async def test_a_closed_result_is_terminal(rig: Rig) -> None:
    state = StopState()
    rig.container.host(WATCH).mkdir(parents=True)
    (rig.container.host(WATCH) / "abort").write_text("")

    decision = await run_stop(rig.site, state, rig.canary)
    again = await run_stop(rig.site, state, rig.canary)

    assert (decision.action, again.action) == ("closed", "closed")
    assert len(stop_calls(rig)) == 1
    assert state.carry == {"words": 0}


def _done_with(**fields: Any) -> ToolOutcome:
    return raw({"status": "done", "message": "", "tokens": {}, "carry": {}, **fields})


JUNK = [
    ToolOutcome(b"not json"),
    ToolOutcome(b"\xff"),
    ToolOutcome(b"[]"),
    stop_json("done", extra=1),
    raw({"status": "done", "message": "", "tokens": {}}),
    stop_json("finished"),
    _done_with(message=1),
    stop_json("resume", "  "),
    _done_with(tokens={"NOPE": "x"}),
    _done_with(tokens={"GUIDANCE": 2}),
    _done_with(carry=[]),
    ToolOutcome(b"[" * 100_000),
    ToolOutcome(stop_json("done").stdout, exit_code=1),
    ToolOutcome(stop_json("done").stdout, exit_code=None, timed_out=True),
    ToolOutcome(stop_json("done").stdout, overflow=True),
    ToolOutcome(b"", exit_code=None, signal=11),
]


@pytest.mark.parametrize("junk", JUNK)
async def test_an_unusable_result_gets_one_repair_then_the_package_closes(
    rig: Rig, junk: ToolOutcome
) -> None:
    rig.container.tool = broken_for_agent(junk)
    state = StopState()

    repair = await run_stop(rig.site, state, rig.canary)
    closed = await run_stop(rig.site, state, rig.canary)

    assert repair.action == "repair"
    assert repair.message
    assert repair.record["attribution"] == "agent_state"
    assert closed.action == "closed"
    assert state.closed
    assert state.rounds == 0  # a repair is not one of the bounded ordinary rounds
    assert closed_flag(rig) in rig.container.root_files


async def test_a_repair_that_works_returns_to_the_ordinary_rounds(rig: Rig) -> None:
    rig.container.tool = broken_for_agent(ToolOutcome(b"junk"))
    state = StopState()
    repair = await run_stop(rig.site, state, rig.canary)
    rig.container.tool = note_tool
    rig.container.write_note()

    done = await run_stop(rig.site, state, rig.canary)

    assert (repair.action, done.action) == ("repair", "done")
    assert not state.closed


async def test_done_with_an_output_missing_is_unusable(rig: Rig) -> None:
    rig.container.tool = broken_for_agent(stop_json("done"))

    decision = await run_stop(rig.site, StopState(), rig.canary)

    assert decision.action == "repair"
    assert "note" in decision.reason


@pytest.mark.parametrize("plant", ["fifo", "symlink", "loop", "deep", "oversized"])
async def test_state_outside_the_bounds_is_the_agents_and_the_tool_is_not_called(
    rig: Rig, plant: str
) -> None:
    state = rig.container.host(WATCH)
    state.mkdir(parents=True)
    if plant == "fifo":
        os.mkfifo(state / "pipe")
    elif plant == "symlink":
        os.symlink("/etc/passwd", state / "note.md")
    elif plant == "loop":
        os.symlink(".", state / "loop")
    elif plant == "deep":
        deep = state
        for index in range(40):
            deep = deep / f"d{index}"
        deep.mkdir(parents=True)
    else:
        with open(state / "big", "wb") as handle:
            handle.truncate(65 << 20)
    run = StopState()

    repair = await run_stop(rig.site, run, rig.canary)
    closed = await run_stop(rig.site, run, rig.canary)

    assert (repair.action, closed.action) == ("repair", "closed")
    assert "outside bounds" in repair.reason
    assert rig.container.calls == []
    assert rig.canary.runs == []


async def test_a_missing_state_directory_is_a_state_the_tool_decides_on(rig: Rig) -> None:
    decision = await run_stop(rig.site, StopState(), rig.canary)

    assert decision.action == "resume"
    assert len(stop_calls(rig)) == 1


async def test_a_failure_with_a_failing_canary_above_the_floors_invalidates_the_trial(
    rig: Rig,
) -> None:
    rig.container.tool = always(ToolOutcome(b"junk"))

    with pytest.raises(TrialInvalid) as invalid:
        await run_stop(rig.site, StopState(), rig.canary)

    assert invalid.value.kind == "instrument"
    assert invalid.value.record["passed"] is False


@pytest.mark.parametrize(
    "floors",
    [
        ResourceFloors({"/app": 10, "/tmp": 10**9}, 10**9),
        ResourceFloors({"/app": 10**9, "/tmp": 10**9}, 10),
    ],
)
async def test_a_failure_with_a_failing_canary_below_the_floors_is_environment_exhausted(
    rig: Rig, floors: ResourceFloors
) -> None:
    rig.container.tool = always(ToolOutcome(b"junk"))
    rig.container.floors = floors

    repair = await run_stop(rig.site, StopState(), rig.canary)

    assert repair.action == "repair"
    assert repair.record["attribution"] == "environment_exhausted"


async def test_tool_changes_and_transport_failures_are_integrity_failures(rig: Rig) -> None:
    rig.container.tampered = True
    with pytest.raises(TrialInvalid) as tampered:
        await run_stop(rig.site, StopState(), rig.canary)
    rig.container.tampered = False
    rig.container.transport_down = True
    with pytest.raises(TrialInvalid) as down:
        await run_stop(rig.site, StopState(), rig.canary)

    assert (tampered.value.kind, down.value.kind) == ("integrity", "integrity")


async def test_stop_snapshots_state_before_and_after_each_call_for_replay(
    rig: Rig, tmp_path: Path
) -> None:
    rig.container.write_note()

    await run_stop(rig.site, StopState(), rig.canary)

    folders = {p.parent.name for p in (tmp_path / "logs" / "stages").rglob("note.md")}
    assert {"stop-0-before", "stop-0-after"} <= folders


async def test_each_calls_output_copy_survives_later_calls_and_edits(rig: Rig) -> None:
    state = StopState()
    rig.container.write_note("one")
    resumed = await run_stop(rig.site, state, rig.canary)
    rig.container.write_note("now a note of enough words")
    done = await run_stop(rig.site, state, rig.canary)

    first = resumed.record["outputs"]["note"]
    last = done.record["outputs"]["note"]
    assert (resumed.action, done.action) == ("resume", "done")
    assert Path(first["host_path"]).read_text() == "one"
    assert Path(last["host_path"]).read_text() == "now a note of enough words"
    assert first["host_path"] != last["host_path"]
    assert first["sha256"] != last["sha256"]


async def test_absent_outputs_do_not_invalidate_a_resume_or_a_close(rig: Rig) -> None:
    state = StopState()
    resumed = await run_stop(rig.site, state, rig.canary)
    rig.container.host(WATCH).mkdir(parents=True, exist_ok=True)
    (rig.container.host(WATCH) / "abort").write_text("")
    closed = await run_stop(rig.site, state, rig.canary)

    assert (resumed.action, closed.action) == ("resume", "closed")
    assert resumed.record["outputs"]["note"]["error"] is not None
    assert closed.record["outputs"]["note"]["sha256"] is None


async def test_an_unusable_stop_result_still_keeps_the_outputs_it_saw(rig: Rig) -> None:
    rig.container.write_note("partial work")
    rig.container.tool = broken_for_agent(ToolOutcome(b"junk"))

    repair = await run_stop(rig.site, StopState(), rig.canary)

    assert repair.action == "repair"
    assert Path(repair.record["outputs"]["note"]["host_path"]).read_text() == "partial work"
