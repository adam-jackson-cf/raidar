"""``acceptance-reply/1`` behaves as the spike's structured build reply did.

A turn's reply is read from its ``last-message.txt``; a syntax fault and a semantic fault are each
repaired once per builder turn in the same session, a second fault of a kind stands, ``ac_tests``
merge across turns, and a regression criterion may go unmapped.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.subject.workflow.acceptance_reply import AcceptanceReply
from raidar.subject.workflow.package_stage import PackageStage
from raidar.subject.workflow.stage import (
    FIRST_BUILD,
    GATE_FIX,
    REPAIR,
    REWORK,
    Checkpoint,
    Resume,
    Spent,
)
from tests.criteria_support import (
    FILE,
    OBJECT,
    REJECTS,
    TESTS,
    Host,
    acceptance,
    reply,
    write_workspace,
)


class Rig:
    """One checkpoint's reply runtime over a workspace the host shell stands in for."""

    def __init__(self, tmp_path: Path, files: dict[str, str] | None = None) -> None:
        self.workspace = tmp_path / "app"
        write_workspace(self.workspace, {FILE: TESTS} if files is None else files)
        self.checkpoint = Checkpoint(Host(), tmp_path / "logs", "task")
        self.acceptance = acceptance()
        self.reply = AcceptanceReply(self.acceptance, str(self.workspace))
        self.turns = 0

    async def turn(self, kind: str, text: str) -> Resume | None:
        self.turns += 1
        after = f"turn-{self.turns}"
        folder = self.checkpoint.logs_dir / after
        folder.mkdir(parents=True)
        (folder / "last-message.txt").write_text(text, encoding="utf-8")
        self.checkpoint.turns.append(Spent(kind, 1.0))
        return await self.reply.check(self.checkpoint, after)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


async def test_a_good_reply_is_kept_and_maps_the_criteria(rig: Rig) -> None:
    resume = await rig.turn(FIRST_BUILD, reply(OBJECT, REJECTS))

    assert resume is None
    assert rig.acceptance.ac_tests == {"F1.AC1": [OBJECT, REJECTS]}
    record = rig.reply.record()
    (kept,) = record["replies"]
    assert (kept["ok"], kept["fault"], kept["reported"]) == (True, None, ["F1.AC1"])
    assert record["repairs"] == {"syntax": 0, "semantic": 0}
    saved = json.loads((rig.checkpoint.logs_dir / kept["record"]).read_text())
    assert saved["value"]["account"] == "built"
    assert len(kept["sha256"]) == 64


async def test_a_reply_with_no_json_is_repaired_once_and_an_unmapped_criterion_once_more(
    rig: Rig,
) -> None:
    first = await rig.turn(FIRST_BUILD, "I finished, no JSON here")
    second = await rig.turn(REPAIR, reply())  # well-formed, but maps nothing to F1.AC1
    third = await rig.turn(REPAIR, reply(OBJECT))

    assert first is not None
    assert first.kind == REPAIR
    assert second is not None
    assert second.kind == REPAIR
    assert "F1.AC1" in second.message
    assert third is None
    faults = [item["fault"] for item in rig.reply.record()["replies"]]
    assert faults == ["syntax", "semantic", None]
    assert rig.reply.record()["repairs"] == {"syntax": 1, "semantic": 1}


async def test_each_kind_of_fault_is_repaired_once_and_a_second_semantic_fault_stands(
    rig: Rig,
) -> None:
    await rig.turn(FIRST_BUILD, reply())
    again = await rig.turn(REPAIR, reply())

    assert again is None  # the second semantic fault of this turn is recorded, not repaired
    assert [item["fault"] for item in rig.reply.record()["replies"]] == ["semantic", "semantic"]
    assert rig.reply.record()["repairs"]["semantic"] == 1


async def test_a_new_builder_turn_is_owed_its_own_repair(rig: Rig) -> None:
    await rig.turn(FIRST_BUILD, reply())
    await rig.turn(REPAIR, reply())

    fresh = await rig.turn(GATE_FIX, reply())

    assert fresh is not None
    assert fresh.kind == REPAIR
    assert rig.reply.record()["repairs"]["semantic"] == 2


async def test_a_mapped_test_in_a_file_the_tree_lacks_is_a_semantic_fault(rig: Rig) -> None:
    resume = await rig.turn(FIRST_BUILD, reply("tests/test_missing.py::test_x"))

    assert resume is not None
    assert "tests/test_missing.py" in resume.message
    (kept,) = rig.reply.record()["replies"]
    assert kept["fault"] == "semantic"


async def test_a_path_outside_the_working_tree_counts_as_absent(rig: Rig, tmp_path: Path) -> None:
    (tmp_path / "outside.py").write_text("def test_o(): ...\n")

    resume = await rig.turn(FIRST_BUILD, reply("../outside.py::test_o"))

    assert resume is not None
    assert "../outside.py" in resume.message


async def test_an_unknown_criterion_is_named_and_the_regression_one_may_go_unmapped(
    rig: Rig,
) -> None:
    text = "Done.\n" + json.dumps(
        {
            "account": "built",
            "adrs": [],
            "ac_tests": [
                {"criterion": "F1.AC1", "tests": [OBJECT]},
                {"criterion": "F9.AC9", "tests": [OBJECT]},
            ],
        }
    )

    resume = await rig.turn(FIRST_BUILD, text)

    assert resume is not None
    assert "F9.AC9" in resume.message
    assert "F1.AC2" not in resume.message  # a Kind: regression criterion needs no test
    assert rig.acceptance.ac_tests == {"F1.AC1": [OBJECT]}


def adr(status: str) -> dict[str, str]:
    return {
        "id": "ADR1",
        "status": status,
        "context": "c",
        "decision": "d",
        "consequences": "q",
    }


async def test_ac_tests_merge_across_turns_and_adrs_keep_the_latest_by_id(rig: Rig) -> None:
    await rig.turn(FIRST_BUILD, reply(OBJECT, adrs=[adr("accepted")]))
    later = await rig.turn(REWORK, reply(REJECTS, adrs=[adr("superseded")]))

    assert later is None
    assert rig.acceptance.ac_tests == {"F1.AC1": [REJECTS]}
    assert rig.reply.record()["adrs"] == [adr("superseded")]


async def test_a_turn_of_another_kind_is_not_read(rig: Rig) -> None:
    resume = await rig.turn("define", "no reply expected")

    assert resume is None
    assert rig.reply.record()["replies"] == []


def test_the_reply_shape_is_appended_to_builder_turns_only(rig: Rig) -> None:
    for kind in (FIRST_BUILD, GATE_FIX, REWORK, REPAIR):
        decorated = rig.reply.decorate(kind, "go")
        assert decorated.startswith("go\n\n")
        assert "ac_tests" in decorated
    assert rig.reply.decorate("define", "go") == "go"


async def test_criteria_unknown_so_far_leave_the_mapping_unchecked(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.reply.acceptance.features = ()

    resume = await rig.turn(FIRST_BUILD, reply())

    assert resume is None  # nothing to map to yet: the gate and the judge decide


async def test_a_refused_gate_is_fixed_before_a_malformed_reply_is_repaired(
    tmp_path: Path,
) -> None:
    """The package lifecycle seals the gate first (``PackageCheckpoint``): a turn whose gate asks
    for a fix is resumed with the fix and its reply is not read; the reply is read, and repaired,
    on the turn after, once the gate's own resume is no longer owed."""
    rig = Rig(tmp_path)
    stage = PackageStage(
        PackageWorkflowDefinition.model_validate(
            {
                "schema": 2,
                "name": "structured",
                "build": {"prompt": "%%%TASK%%%", "reply": "acceptance-reply/1"},
            }
        ),
        locked={},
        pack="python",
        skills_placement=".agents/skills",
    )
    stage.reply = rig.reply
    answers = [Resume("fix the gate", GATE_FIX), None]

    async def gate_check(after: str) -> Resume | None:
        return answers.pop(0)

    stage.checkpoint = cast(Any, SimpleNamespace(check=gate_check))
    for number, kind in ((1, FIRST_BUILD), (2, GATE_FIX)):
        folder = rig.checkpoint.logs_dir / f"turn-{number}"
        folder.mkdir(parents=True)
        (folder / "last-message.txt").write_text("no JSON here", encoding="utf-8")
        rig.checkpoint.turns.append(Spent(kind, 1.0))

    first = await stage.check(rig.checkpoint, "turn-1")
    assert first is not None
    assert first.kind == GATE_FIX
    assert rig.reply.record()["replies"] == []  # the gate's resume won; nothing was read

    second = await stage.check(rig.checkpoint, "turn-2")
    assert second is not None
    assert second.kind == REPAIR
    assert [item["fault"] for item in rig.reply.record()["replies"]] == ["syntax"]
