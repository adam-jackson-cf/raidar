"""``setup/1`` contract: strict tokens, and any setup failure invalidates the trial."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from raidar.subject.primitive.setup import run_setup
from raidar.subject.primitive.site import TrialInvalid
from tests.lifecycle_support import (
    WATCH,
    Rig,
    ToolOutcome,
    build_rig,
)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return build_rig(tmp_path, monkeypatch)


def _tokens(document: Any) -> ToolOutcome:
    return ToolOutcome(json.dumps(document).encode())


async def test_setup_returns_every_declared_token_and_calls_with_the_context(rig: Rig) -> None:
    result = await run_setup(rig.site)

    assert result.tokens == {"GUIDANCE": "Say what you changed."}
    primitive, argv, context = rig.container.calls[0]
    assert (primitive, argv) == ("setup/1", ["note-state", "setup"])
    assert context == {
        "task": "the task",
        "params": {"min_words": 3},
        "round": 0,
        "carry": {},
        "state": WATCH,
        "outputs": {"note": f"{WATCH}/note.md"},
    }


async def test_setup_copies_outputs_that_exist_and_tolerates_absent_ones(rig: Rig) -> None:
    absent = await run_setup(rig.site)
    rig.container.write_note("seeded by setup")
    present = await run_setup(rig.site)

    assert absent.record["outputs"]["note"]["error"] is not None
    copied = present.record["outputs"]["note"]
    assert Path(copied["host_path"]).read_text() == "seeded by setup"


@pytest.mark.parametrize(
    "outcome",
    [
        ToolOutcome(b"{}", exit_code=1),
        ToolOutcome(b"{}", exit_code=None, timed_out=True),
        ToolOutcome(b"{}", overflow=True),
        ToolOutcome(b"not json"),
        ToolOutcome(b"\xff\xfe"),
        _tokens({"tokens": {}}),
        _tokens({"tokens": {"GUIDANCE": "x", "EXTRA": "y"}}),
        _tokens({"tokens": {"GUIDANCE": 3}}),
        _tokens({"tokens": {"GUIDANCE": "x"}, "note": "x"}),
        _tokens(["GUIDANCE"]),
        _tokens({"tokens": ["GUIDANCE"]}),
    ],
)
async def test_a_failed_or_malformed_setup_invalidates_the_trial(
    rig: Rig, outcome: ToolOutcome
) -> None:
    rig.container.tool = lambda *_: outcome

    with pytest.raises(TrialInvalid) as invalid:
        await run_setup(rig.site)

    assert invalid.value.kind == "instrument"


async def test_setup_refuses_unsafe_state_without_calling_the_tool(rig: Rig) -> None:
    state = rig.container.host(WATCH)
    state.mkdir(parents=True)
    os.mkfifo(state / "pipe")

    with pytest.raises(TrialInvalid, match="state refused"):
        await run_setup(rig.site)

    assert rig.container.calls == []


async def test_setup_keeps_host_side_state_snapshots_for_replay(rig: Rig, tmp_path: Path) -> None:
    rig.container.write_note()

    result = await run_setup(rig.site)

    assert result.record["state"]["before"]
    assert result.record["state"]["after"]
    kept = list((tmp_path / "logs" / "stages").rglob("note.md"))
    assert {path.parent.name for path in kept} == {"setup-before", "setup-after", "note"}


async def test_transport_failure_during_setup_is_an_integrity_failure(rig: Rig) -> None:
    rig.container.transport_down = True

    with pytest.raises(TrialInvalid) as invalid:
        await run_setup(rig.site)

    assert invalid.value.kind == "integrity"


async def test_changed_tool_bytes_during_setup_are_an_integrity_failure(rig: Rig) -> None:
    rig.container.tampered = True

    with pytest.raises(TrialInvalid) as invalid:
        await run_setup(rig.site)

    assert invalid.value.kind == "integrity"
