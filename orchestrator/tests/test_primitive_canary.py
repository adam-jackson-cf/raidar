"""The ``done`` canary reproduces its ``stage-check`` record before the agent starts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive.calls import ResourceFloors
from raidar.subject.primitive.canary import (
    AGENT_STATE,
    ENVIRONMENT_EXHAUSTED,
    StageCheckRecord,
    run_fixture,
)
from raidar.subject.primitive.site import TrialInvalid
from tests.lifecycle_support import (
    PACK,
    PRIMITIVES,
    Rig,
    ToolOutcome,
    build_rig,
    note_digest,
    note_tool,
    stop_json,
)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return build_rig(tmp_path, monkeypatch)


async def require(rig: Rig) -> None:
    await rig.canary.require(rig.package.digest, PACK, PRIMITIVES)


async def test_the_fixture_runs_in_its_own_state_and_reproduces_the_record(rig: Rig) -> None:
    await require(rig)

    (primitive, argv, context) = rig.container.calls[0]
    assert (primitive, argv) == ("stop/1", ["note-state", "inspect"])
    assert "/canary/" in context["state"]
    assert context["outputs"]["note"].startswith(context["state"])
    assert rig.canary.runs[0]["passed"] is True
    assert not rig.container.host("/app/.raidar").exists()  # the agent's state is untouched


@pytest.mark.parametrize(
    "outcome",
    [
        stop_json("done", carry={"words": 99}),
        ToolOutcome(b"junk"),
        ToolOutcome(stop_json("done", carry={"words": 5}).stdout, exit_code=1),
    ],
)
async def test_a_changed_or_failing_fixture_result_invalidates_the_trial(
    rig: Rig, outcome: ToolOutcome
) -> None:
    rig.container.tool = lambda *_: outcome

    with pytest.raises(TrialInvalid) as invalid:
        await require(rig)

    assert invalid.value.kind == "instrument"
    assert invalid.value.record["passed"] is False


async def test_changed_output_bytes_invalidate_the_trial(rig: Rig) -> None:
    def tool(container: Any, argv: Any, context: dict[str, Any]) -> ToolOutcome:
        outcome = note_tool(container, argv, context)
        container.host(context["outputs"]["note"]).write_text("tampered")
        return outcome

    rig.container.tool = tool

    with pytest.raises(TrialInvalid):
        await require(rig)


@pytest.mark.parametrize(
    "mismatch",
    [
        {"package": "0" * 64},
        {"pack": "other-pack"},
        {"primitives": {"stop/1": "q" * 64}},
    ],
)
async def test_a_record_for_other_identities_is_refused(rig: Rig, mismatch: dict[str, Any]) -> None:
    rig.canary.expected = StageCheckRecord(**{**rig.expected.__dict__, **mismatch})

    with pytest.raises(TrialInvalid, match="stage-check record"):
        await require(rig)

    assert rig.container.calls == []


def test_the_workflow_may_carry_primitives_the_record_does_not_name(rig: Rig) -> None:
    union = {**PRIMITIVES, "gate/1": "g" * 64}

    assert rig.expected.identity_error(rig.package.digest, PACK, union) is None


@pytest.mark.parametrize("workflow", [{}, {"stop/1": "q" * 64}, {"gate/1": "g" * 64}])
def test_a_primitive_the_record_names_must_be_present_and_equal(
    rig: Rig, workflow: dict[str, str]
) -> None:
    error = rig.expected.identity_error(rig.package.digest, PACK, workflow)

    assert error is not None
    assert "stop/1" in error


async def test_tool_tree_changes_and_failed_exec_are_integrity_failures(rig: Rig) -> None:
    rig.container.tampered = True
    with pytest.raises(TrialInvalid) as tampered:
        await require(rig)
    rig.container.tampered = False
    rig.container.transport_down = True
    with pytest.raises(TrialInvalid) as exec_failed:
        await require(rig)
    rig.container.transport_down = False
    rig.container.upload_down = True
    with pytest.raises(TrialInvalid) as upload_failed:
        await require(rig)

    kinds = (tampered.value.kind, exec_failed.value.kind, upload_failed.value.kind)
    assert kinds == ("integrity",) * 3


async def test_a_failure_with_a_passing_canary_is_the_agents(rig: Rig) -> None:
    assert await rig.canary.attribute_failure() == AGENT_STATE


async def test_a_failure_with_a_failing_canary_is_the_instruments_above_the_floors(
    rig: Rig,
) -> None:
    rig.container.tool = lambda *_: ToolOutcome(b"junk")

    with pytest.raises(TrialInvalid) as invalid:
        await rig.canary.attribute_failure()

    assert invalid.value.kind == "instrument"


async def test_exhausted_disk_makes_a_failing_canary_the_agents(rig: Rig) -> None:
    rig.container.tool = lambda *_: ToolOutcome(b"junk")
    rig.container.floors = ResourceFloors({"/app": 5, "/tmp": None}, None)

    assert await rig.canary.attribute_failure() == ENVIRONMENT_EXHAUSTED


@pytest.mark.parametrize("failure", ["upload_down", "transport_down"])
async def test_transport_failure_remains_invalid_below_resource_floors(
    rig: Rig, failure: str
) -> None:
    setattr(rig.container, failure, True)
    rig.container.floors = ResourceFloors({"/app": 5}, 5)

    with pytest.raises(TrialInvalid) as invalid:
        await rig.canary.attribute_failure()

    assert invalid.value.kind == "integrity"


async def test_a_canary_that_cannot_be_staged_with_room_left_is_an_integrity_failure(
    rig: Rig,
) -> None:
    rig.container.upload_down = True

    with pytest.raises(TrialInvalid) as invalid:
        await rig.canary.attribute_failure()

    assert invalid.value.kind == "integrity"


async def test_run_fixture_replays_any_fixture_case_for_stage_check(rig: Rig) -> None:
    run = await run_fixture(rig.site, "resume", "resume-0")

    assert run.usable
    assert run.outputs == note_digest(rig.container, "resume")
    assert run.stdout_sha256 != rig.expected.stdout_sha256


def test_the_stage_check_record_round_trips_as_json(rig: Rig) -> None:
    again = StageCheckRecord.from_json(rig.expected.as_json())

    assert again == rig.expected
    assert again.memory_available == 1_000
    no_memory = {**rig.expected.as_json(), "floors": {"disk_free": 1, "memory_available": None}}
    assert StageCheckRecord.from_json(no_memory).memory_available is None
