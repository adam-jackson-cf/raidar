"""What the define state holds, as the stage reads it when the builder stops."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import cast

import pytest

from define_tool import schemas, status, tool
from define_tool.draft import DraftError
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


def test_a_state_with_no_draft_is_unbuilt_and_checked_as_empty(state: Path) -> None:
    code, held = run_status(state)

    assert code == tool.FAULT
    assert held["state"] == "unbuilt"
    assert held["blocking"]


def test_an_unsealed_draft_reports_the_findings_the_builder_is_to_repair(state: Path) -> None:
    build(state, dbv.ready(dbv.EMPTY))

    code, held = run_status(state)

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


def test_a_sealed_ask_hands_over_its_questions(state: Path) -> None:
    build(state, dbv.asks(), "ask")

    code, held = run_status(state)

    assert (code, held["state"]) == (tool.FAULT, "asking")
    questions = held["reply"]["questions"]  # type: ignore[index]
    assert [item["proposal"] for item in questions] == ["F1 Greeting reports liveness only."]


def test_a_draft_changed_after_done_is_no_longer_sealed(state: Path) -> None:
    build(state, dbv.ready(), "ready")
    tool.run(state, ["put"], lambda: "title: Another title\n")

    _, held = run_status(state)

    assert held["state"] == "unsealed"


def test_an_unreadable_state_is_an_error(state: Path) -> None:
    (state / tool.STATE_FILE).write_text("{", encoding="utf-8")

    code, held = run_status(state)

    assert (code, held["state"]) == (tool.BROKEN, "error")
    with pytest.raises(DraftError):
        status.assess(state)


def test_a_tool_failure_is_reported_apart_from_the_findings(state: Path) -> None:
    (state / tool.STATE_FILE).write_text("{", encoding="utf-8")
    tool.run(state, ["show"], lambda: "")

    assert any(call.command == tool.ERROR_CALL for call in tool.read_calls(state))
    (state / tool.STATE_FILE).unlink()
    assert status.assess(state)["tool_failed"] is True


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
