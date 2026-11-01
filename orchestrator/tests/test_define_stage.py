"""The ``raidar/define`` package: its setup, stop and gate calls, and parity with the spike stage.

The fixtures under ``lab/stages/raidar/define/fixtures`` are run through the package's own
``define_tool.stage`` module on copies of their state, as the container would run them, and the
calls are compared with the spike ``Define`` stage's behaviour: the units, the repair message, the
accepted-question round, the endings and the criteria a judge reads.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from raidar_contract.criteria import validate_criteria

from define_tool import compact, stage, tool
from define_tool.unitrules import VERSIONS
from raidar.contract.paths import stages_root
from raidar.subject.workflow.acceptance import Acceptance
from tests import define_support as dbv

PACKAGE = stages_root() / "raidar" / "define"
FIXTURES = sorted(path.name for path in (PACKAGE / "fixtures").iterdir())
PARAMS = {"unit_rules": 2, "max_refinement_rounds": 2}
GATE_CASES = {"pass": 0, "refuse": tool.FAULT}


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


def fixture_run(tmp_path: Path, case: str) -> tuple[Any, dict[str, Any], dict[str, str]]:
    """Run fixture ``case`` on a copy of its state: (result, its expectation, output digests)."""
    source = PACKAGE / "fixtures" / case
    expected = yaml.safe_load((source / "expect.yaml").read_text(encoding="utf-8"))
    document = json.loads((source / "context.json").read_text(encoding="utf-8"))
    gate = case in GATE_CASES
    work = tmp_path / "work"
    shutil.copytree(source / "state", work)
    watched = work / ".raidar" / "define" if gate else work
    document["state"] = str(watched)
    document["outputs"] = {
        "criteria": str(watched / "criteria.json"),
        "definition": str(tmp_path / "definition.md"),
    }
    code, out = call(tmp_path, document, "sealed" if gate else "inspect")
    result: Any = code if gate else json.loads(out)
    digests = {
        name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for name, path in document["outputs"].items()
        if Path(path).exists()
    }
    return result, expected, digests


@pytest.mark.parametrize("case", FIXTURES)
def test_every_fixture_meets_its_expectation(tmp_path: Path, case: str) -> None:
    result, expected, digests = fixture_run(tmp_path, case)

    if case in GATE_CASES:
        assert result == GATE_CASES[case]
        assert (result == 0) == (expected["status"] == "pass")
    else:
        assert set(result) == {"status", "message", "tokens", "carry"}
        assert result["status"] == expected["status"]
        assert digests == (expected["outputs"] or {})


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

    with pytest.raises(SystemExit) as raised:
        stage.main(["inspect"])

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


def test_a_ready_draft_is_done_and_writes_both_outputs(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.ready(), "ready")
    document = context(tmp_path, state)

    _, out = call(tmp_path, document, "inspect")

    assert json.loads(out)["status"] == "done"
    rendered = Path(document["outputs"]["definition"]).read_text(encoding="utf-8")
    written = json.loads((state / "criteria.json").read_text(encoding="utf-8"))
    assert written == stage.criteria_document(rendered)
    assert written["contract"] == "criteria/1"


# --- gate: the seal ------------------------------------------------------------------------------


def test_the_seal_names_what_blocks_and_changes_nothing(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.ready())
    before = sorted((p.name, p.read_bytes()) for p in state.iterdir())

    code, out = call(tmp_path, context(tmp_path, state), "sealed")

    assert code == tool.FAULT
    assert out.startswith("definition_not_ready: ")
    assert sorted((p.name, p.read_bytes()) for p in state.iterdir()) == before


def test_a_sealed_ready_draft_passes_the_prerequisite(tmp_path: Path) -> None:
    state = draft(tmp_path, dbv.ready(), "ready")

    code, out = call(tmp_path, context(tmp_path, state), "sealed")

    assert (code, out) == (0, "")


# --- criteria/1: what the judge reads ------------------------------------------------------------


def judge_view(acceptance: Acceptance) -> list[dict[str, Any]]:
    """What the spike judge reads of each criterion, as ``criteria/1`` fields."""
    return [
        {
            "id": item.id,
            "criterion": item.text,
            "verification": item.verification,
            "sources": list(item.sources),
            "kind": "regression" if item.id in acceptance.optional else "new",
            "aspects": [
                {"situation": probe.situation, "outcome": probe.outcome} for probe in item.aspects
            ],
            "examples": [
                {"situation": probe.given, "outcome": probe.expected} for probe in item.examples
            ],
        }
        for item in acceptance.criteria
    ]


@pytest.mark.parametrize("regression", [False, True])
def test_criteria_hold_exactly_what_the_spike_judge_reads(tmp_path: Path, regression: bool) -> None:
    criteria = [dbv.criterion(i) for i in dbv.AC]
    if regression:
        criteria[-1] = dbv.regressed(dbv.AC[-1])
    state = draft(tmp_path, dbv.ready(definition=dbv.body(criteria)), "ready")
    document = context(tmp_path, state)
    call(tmp_path, document, "inspect")
    rendered = Path(document["outputs"]["definition"]).read_text(encoding="utf-8")
    acceptance = Acceptance()
    acceptance.load(rendered)

    written = json.loads((state / "criteria.json").read_text(encoding="utf-8"))

    checked = validate_criteria(written)
    flat = [item.model_dump(mode="json") for item in checked.all_criteria()]
    assert flat == judge_view(acceptance)
    assert [item["kind"] for item in flat].count("regression") == int(regression)
    assert all(not item["aspects"] for item in flat if item["kind"] == "regression")
    assert {f["id"] for f in written["features"]} == {f.id for f in acceptance.features}
