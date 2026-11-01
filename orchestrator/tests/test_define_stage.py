"""The ``raidar/define`` package's setup, bounded stop and gate behavior.

Package fixtures exercise units, repair, accepted-question rounds, closure and sealed state.
"""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from define_tool import compact, stage, tool
from define_tool.draft import Draft
from define_tool.unitrules import VERSIONS
from tests import define_support as dbv

PARAMS = {"unit_rules": 2, "max_refinement_rounds": 2}


def context(work: Path, state: Path, **over: Any) -> dict[str, Any]:
    """A call context over ``state`` with its outputs inside ``work``."""
    value = {
        "task": dbv.BODY,
        "params": PARAMS,
        "round": 0,
        "carry": {},
        "state": str(state),
        "outputs": {
            "criteria": str(state / "criteria.json"),
            "definition": str(work / "feature-definition.md"),
        },
    }
    return {**value, **over}


def call(work: Path, document: dict[str, Any], *argv: str) -> tuple[int, str]:
    work.mkdir(parents=True, exist_ok=True)
    path = work / "context.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = stage.main([*argv, "--context", str(path)])
    return code, out.getvalue()


def draft(tmp_path: Path, reply: dict[str, Any], *sealed: str) -> Path:
    """A define state holding the draft ``reply`` builds, sealed with each of ``sealed``."""
    state = tmp_path / "drafted" / "state"
    tool.start(state, tool.Context(dbv.BODY, (), "", (), str(state.parent), 2))
    tool.run(state, ["put"], lambda: dbv.block_of(reply))
    for word in sealed:
        tool.run(state, ["done", word], lambda: "")
    return state


@pytest.mark.parametrize("saved", [b"", b"{", b"\xff", b"[" * 50 + b"]" * 50])
def test_corrupt_agent_draft_requests_one_repair_then_closes(tmp_path: Path, saved: bytes) -> None:
    state = tmp_path / "state"
    state.mkdir()
    path = state / tool.STATE_FILE
    path.write_bytes(saved)
    document = context(tmp_path, state)

    code, output = call(tmp_path, document, "inspect")
    first = json.loads(output)
    assert code == 0
    assert first["status"] == "resume"
    code, output = call(tmp_path, {**document, "carry": first["carry"]}, "inspect")
    assert code == 0
    assert json.loads(output)["status"] == "closed"
    code, _ = call(tmp_path, document, "sealed")
    assert code == tool.FAULT
    assert path.read_bytes() == saved


# --- setup ---------------------------------------------------------------------------------------


def test_setup_returns_the_units_the_spike_stage_numbers(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "stale.json").write_text("{}", encoding="utf-8")
    definition = tmp_path / "feature-definition.md"
    definition.write_text("old", encoding="utf-8")

    code, out = call(tmp_path, context(tmp_path, state), "setup")

    spike = compact.units(dbv.BODY, [], VERSIONS[2]).text()
    assert code == 0
    assert json.loads(out) == {"tokens": {"UNITS": spike}}
    assert not (state / "stale.json").exists()
    assert (state / tool.CONTEXT_FILE).is_file()
    assert not definition.exists()


@pytest.mark.parametrize("rules", [3, "2", True, None])
def test_setup_refuses_a_unit_rule_set_that_does_not_exist(tmp_path: Path, rules: object) -> None:
    state = tmp_path / "state"
    state.mkdir()
    document = context(tmp_path, state, params={**PARAMS, "unit_rules": rules})

    code, out = call(tmp_path, document, "setup")

    assert code == tool.BROKEN
    assert "unit_rules" in json.loads(out)["error"]


def test_a_call_without_state_fails_with_the_tool_exit_code(tmp_path: Path) -> None:
    document = context(tmp_path, tmp_path / "state")
    del document["state"]

    code, out = call(tmp_path, document, "inspect")

    assert code == tool.BROKEN
    assert "state" in json.loads(out)["error"]


def test_the_context_comes_from_the_environment_for_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = draft(tmp_path, dbv.ready(), "ready")
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps(context(tmp_path, state)), encoding="utf-8")
    monkeypatch.setenv(stage.CONTEXT_ENV, str(path))

    assert stage.main(["sealed"]) == 0


def test_a_call_without_any_context_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(stage.CONTEXT_ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["define_tool.stage", "inspect"])
    monkeypatch.delitem(sys.modules, "define_tool.stage")

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("define_tool.stage", run_name="__main__")

    assert raised.value.code == 2


def test_a_context_that_is_not_an_object_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "ctx.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        stage.main(["inspect", "--context", str(path)])

    assert raised.value.code == 2


# --- stop: repair, clarification, endings -------------------------------------------------------


def test_an_unsealed_draft_is_repaired_once_then_closed(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.ready())

    code, out = call(tmp_path, context(tmp_path, state), "inspect")
    result = json.loads(out)

    assert code == 0
    assert result["status"] == "resume"
    assert result["carry"] == {"repaired": True}
    _, again = call(tmp_path, context(tmp_path, state, carry=result["carry"]), "inspect")
    assert json.loads(again)["status"] == "closed"
    assert not (state / "criteria.json").exists()


def test_accepted_proposals_reach_the_next_turn_and_later_rounds(tmp_path: Path) -> None:
    reply = dbv.asks()
    state = draft(tmp_path, reply, "ask")

    _, out = call(tmp_path, context(tmp_path, state), "inspect")
    result = json.loads(out)

    assert result["status"] == "resume"
    assert result["carry"]["asked"] == 1
    for question in reply["questions"]:
        assert question["proposal"] in result["message"]
        assert any(question["proposal"] in answer for answer in result["carry"]["said"])
    assert not (state / "criteria.json").exists()


def test_more_rounds_of_questions_than_the_params_allow_close_the_stage(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.asks(), "ask")

    _, out = call(tmp_path, context(tmp_path, state, carry={"asked": 2}), "inspect")

    assert json.loads(out)["status"] == "closed"


# --- gate: the seal ------------------------------------------------------------------------------


def test_the_seal_names_what_blocks_and_changes_nothing(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.ready())
    before = sorted((p.name, p.read_bytes()) for p in state.iterdir())

    code, out = call(tmp_path, context(tmp_path, state), "sealed")

    assert code == tool.FAULT
    assert out.startswith("definition_not_ready: ")
    assert sorted((p.name, p.read_bytes()) for p in state.iterdir()) == before


def test_a_sealed_ready_draft_passes_the_prerequisite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = dbv.ready(dbv.body([dbv.criterion(dbv.AC[0]), dbv.regressed(dbv.AC[1])]))
    held = Draft(dbv.items(reply), "ready")
    monkeypatch.setattr(tool, "read_draft", lambda _: held)
    monkeypatch.setattr(tool, "read_calls", lambda _: [])
    written: dict[Path, str] = {}
    monkeypatch.setattr(tool, "write_definition", written.__setitem__)
    document = context(Path("/output"), Path("/state"))

    assert stage.sealed(document) == 0
    assert written == {}
    assert stage.inspect(document, PARAMS)["status"] == "done"

    definition = written[Path("/output/feature-definition.md")]
    assert "GET /greet" in definition
    verification = next(
        line.strip().removeprefix("Verification: ")
        for line in definition.splitlines()
        if line.strip().startswith("Verification: ")
    )
    criteria = json.loads(written[Path("/state/criteria.json")])
    assert criteria["contract"] == "criteria/1"
    (feature,) = criteria["features"]
    assert (feature["id"], feature["name"], feature["outcome"]) == (
        "F1",
        "Greeting",
        "GET /greet answers with a greeting for the caller's name",
    )
    behaviour, regression = feature["criteria"]
    assert behaviour == {
        "id": "F1.AC1",
        "criterion": dbv.TEXTS["F1.AC1"],
        "verification": verification,
        "sources": [
            "GET /greet returns 200 with the text `hello <name>`, where `name` is the "
            "`name` query parameter.",
            "Add a greeting endpoint.",
        ],
        "kind": "new",
        "aspects": [
            {"situation": situation, "outcome": outcome}
            for situation, outcome in dbv.ASPECTS["F1.AC1"]
        ],
        "examples": [
            {"situation": "GET /greet with `name=Ada`", "outcome": "`200` and `hello Ada`"}
        ],
    }
    assert regression["id"] == "F1.AC2"
    assert (regression["kind"], regression["aspects"], regression["examples"]) == (
        "regression",
        [],
        [],
    )
