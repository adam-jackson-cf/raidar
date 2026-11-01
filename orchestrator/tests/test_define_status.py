"""What the define state holds, as the stage reads it when the builder stops."""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from define_tool import block, schemas, status, tool
from define_tool.draft import Draft, DraftError
from tests import define_support as dbv


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    tool.start(directory, tool.Context(dbv.BODY, (), "(first round)", (), str(tmp_path / "app"), 2))
    return directory


def build(state: Path, reply: dict[str, object], *done: str) -> None:
    tool.run(state, ["put"], lambda: dbv.block_of(reply))
    for word in done:
        tool.run(state, ["done", word], lambda: "")


def run_status(state: Path, *args: str) -> tuple[int, dict[str, object]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = status.main(list(args), {tool.STATE_ENV: str(state)})
    return code, json.loads(out.getvalue())


@pytest.fixture
def memory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Draft | None, tuple[tool.Call, ...]], tuple[int, dict[str, object]]]:
    def assess(
        draft: Draft | None, calls: tuple[tool.Call, ...] = ()
    ) -> tuple[int, dict[str, object]]:
        monkeypatch.setattr(status, "_context", lambda _: tool.Context(dbv.BODY, (), "", (), "", 2))
        monkeypatch.setattr(tool, "read_draft", lambda _: draft)
        monkeypatch.setattr(tool, "read_calls", lambda _: list(calls))
        return run_status(Path("/state"))

    return assess


def test_a_state_with_no_draft_is_unbuilt_and_checked_as_empty(memory: Callable[..., Any]) -> None:
    code, held = memory(None)

    assert code == tool.FAULT
    assert held["state"] == "unbuilt"
    assert held["blocking"]


def test_an_unsealed_draft_reports_the_findings_the_builder_is_to_repair(
    memory: Callable[..., Any],
) -> None:
    code, held = memory(Draft(dbv.items(dbv.ready(dbv.EMPTY))))

    assert code == tool.FAULT
    assert held["state"] == "unsealed"
    findings = cast(list[dict[str, object]], held["findings"])
    assert held["blocking"] == [f["text"] for f in findings if not f["advisory"]]
    assert held["blocking"]


def test_a_sealed_passing_definition_is_ready_and_written_on_request(
    state: Path, tmp_path: Path
) -> None:
    build(state, dbv.ready(), "ready")
    output = tmp_path / "out" / "feature-definition.md"

    code, held = run_status(state, "--write", "--output", str(output))

    assert (code, held["state"], held["blocking"], held["schema_violations"]) == (
        0,
        "ready",
        [],
        [],
    )
    file = held["checked"]["file"]  # type: ignore[index]
    assert output.read_text(encoding="utf-8") == file["rendered"]
    assert held["tool_failed"] is False
    assert held["draft"] == {"present": True, "sealed": "ready"}


def test_a_sealed_ask_hands_over_its_questions(memory: Callable[..., Any]) -> None:
    code, held = memory(Draft(dbv.items(dbv.asks()), "ask"))

    assert (code, held["state"]) == (tool.FAULT, "asking")
    questions = held["reply"]["questions"]  # type: ignore[index]
    assert [item["proposal"] for item in questions] == ["F1 Greeting reports liveness only."]


def test_a_draft_changed_after_done_is_no_longer_sealed(memory: Callable[..., Any]) -> None:
    draft = Draft(dbv.items(dbv.ready()), "ready")
    draft.put([block.Item(block.TITLE, block.TITLE, {"text": "Another title"})])
    _, held = memory(draft)

    assert held["state"] == "unsealed"


def test_an_unreadable_state_is_an_error(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (state / tool.STATE_FILE).write_text("{", encoding="utf-8")
    monkeypatch.setenv(tool.STATE_ENV, str(state))
    monkeypatch.setattr("sys.argv", ["define_tool.status"])
    monkeypatch.delitem(sys.modules, "define_tool.status")

    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("define_tool.status", run_name="__main__")

    held = json.loads(capsys.readouterr().out)
    assert (stopped.value.code, held["state"]) == (tool.BROKEN, "error")
    with pytest.raises(DraftError):
        status.assess(state)


def test_a_tool_failure_is_reported_apart_from_the_findings(memory: Callable[..., Any]) -> None:
    code, held = memory(None, (tool.Call(tool.ERROR_CALL),))

    assert (code, held["state"], held["tool_failed"]) == (tool.FAULT, "unbuilt", True)
    assert held["blocking"]


def test_the_reply_a_draft_assembles_keeps_to_the_reply_schema() -> None:
    assert schemas.violations(dbv.ready(), schemas.INTAKE_REPLY_SCHEMA) == []
    assert schemas.violations(dbv.asks(), schemas.INTAKE_REPLY_SCHEMA) == []


def test_a_reply_that_breaks_the_schema_is_named_by_path() -> None:
    reply = dbv.ready()
    reply["status"] = "done"
    del reply["questions"]
    criteria = reply["definition"]["features"][0]["criteria"]  # type: ignore[index]
    criteria[0]["kind"] = "other"
    criteria[1]["sources"] = "U1"

    found = schemas.violations(reply, schemas.INTAKE_REPLY_SCHEMA)

    assert found == [
        "/: 'questions' is a required property",
        "/status: 'done' is not one of ['ready', 'needs_input']",
        "/definition/features/0/criteria/0/kind: 'other' is not one of ['behaviour', 'regression']",
        "/definition/features/0/criteria/1/sources: 'U1' is not of type 'array'",
    ]


def test_the_violations_are_cut_short() -> None:
    reply: dict[str, object] = {"features": [{}] * 12}

    found = schemas.violations(reply, schemas.INTAKE_REPLY_SCHEMA)

    assert len(found) == schemas.MAX_VIOLATIONS + 1
    assert found[-1].startswith("(and ")


def test_a_context_given_to_the_check_replaces_the_one_in_the_state(
    state: Path, tmp_path: Path
) -> None:
    build(state, dbv.ready(), "ready")
    host = tmp_path / "host-context.json"
    other = tool.Context("# Other\n\nSomething else is asked of this task.\n", (), "", (), "", 2)
    host.write_text(json.dumps(other.record()), encoding="utf-8")

    code, held = run_status(state, "--context", str(host))

    assert (code, held["state"]) == (tool.FAULT, "unusable")
    assert held["blocking"]
    assert status.assess(state)["state"] == "ready"
