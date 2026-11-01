"""What a definition owes the probes: examples for arrows, list items and underivable examples."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from define_tool import compact, owed, tool
from define_tool.core import JsonObject
from define_tool.draft import SEALS
from define_tool.intakecheck import check_reply, with_probes
from define_tool.kinds import Kind
from define_tool.owed import (
    Arrow,
    Case,
    ListUnit,
    Loose,
    Owed,
    Section,
)
from define_tool.unitrules import V2

ISSUE = """\
# Calc

Build `greet.py`; it reads JSON from STDIN and writes JSON to STDOUT.

Unknown operators → `BAD_OP`.

Operators: `+`, `-`, `*`.

`div` by zero → `ZERO`.

The cycle 1 → 3 → 2 → 1 is rejected.

Names map to a result → done.
"""
UNITS = compact.units(ISSUE, [], V2)


def reply(*criteria: JsonObject) -> JsonObject:
    return {
        "status": "ready",
        "definition": {"features": [{"id": "F1", "criteria": list(criteria)}]},
    }


def criterion(ident: str, sources: str, *examples: tuple[str, str]) -> JsonObject:
    return {
        "id": ident,
        "kind": "behaviour",
        "sources": [sources],
        "examples": [{"input": given, "expected": want} for given, want in examples],
    }


def test_the_arrow_outcomes_of_an_issue_are_read_with_the_literals_on_each_side() -> None:
    good, loose = owed.arrows(UNITS)

    assert good == (Arrow("U2", None, "BAD_OP"), Arrow("U4", "div", "ZERO"))
    assert [(item.id, item.reason) for item in loose] == [
        ("U5", "`U5` has 3 arrows, not one input-to-outcome pair"),
        ("U6", "`U6` names no backticked outcome after its arrow"),
    ]


def test_a_labelled_list_unit_is_a_list_with_its_items() -> None:
    assert owed.lists(UNITS) == (ListUnit("U3", "Operators", ("+", "-", "*")),)


@pytest.mark.parametrize(
    ("given", "parts"),
    [
        ("`python greet.py run`", ("python greet.py run",)),
        ("`run`.", ("run",)),
        ("`--execute` with `{}`", ("--execute", "{}")),
        ("`a` and `b`", ("a", "b")),
        ("`a`, `b`", ("a", "b")),
        ("a plan naming `fern`", ()),
        ("`a` with a plan `b`", ()),
        ("`a` `b` `c`", ()),
        ("given `a`", ()),
        ("plain text", ()),
        ("", ()),
    ],
)
def test_an_examples_input_is_the_program_s_input_in_backticks(
    given: str, parts: tuple[str, ...]
) -> None:
    case = Case("F1.AC1", ("U1",), given, "`x`")

    assert case.parts == parts
    assert case.text == "\n".join(parts)


def test_the_cases_of_a_reply_are_the_examples_of_its_behaviour_criteria() -> None:
    body = reply(
        criterion("F1.AC1", "U2", ("`x`", "`BAD_OP`"), ("`y`", "`z`")),
        {"id": "F1.AC2", "kind": "regression", "sources": ["U1"], "examples": []},
    )

    found = owed.cases(body)

    assert [(c.criterion, c.index, c.units, c.given) for c in found] == [
        ("F1.AC1", 1, ("U2",), "`x`"),
        ("F1.AC1", 2, ("U2",), "`y`"),
    ]
    assert owed.cases({"status": "ready"}) == ()
    assert owed.cases({"definition": {"features": [{"criteria": [{"kind": "regression"}]}]}}) == ()


def test_an_arrow_needs_an_example_with_its_left_literal_in_and_its_right_out() -> None:
    arrow = Arrow("U4", "div", "ZERO")

    def carried(sources: str, given: str, want: str) -> bool:
        return owed.carries_arrow(Case("F1.AC1", (sources,), given, want), arrow)

    assert carried("U4", "`div 1 0`", "`ZERO`")
    assert not carried("U2", "`div 1 0`", "`ZERO`")
    assert not carried("U4", "`add 1 0`", "`ZERO`")
    assert not carried("U4", "`div 1 0`", "`OK`")
    # Prose is no input a probe could run.
    assert not carried("U4", "dividing by zero", "`ZERO`")
    # An arrow with no left literal needs only its outcome.
    assert owed.carries_arrow(
        Case("F1.AC1", ("U2",), "`bad`", "`BAD_OP`"), Arrow("U2", None, "BAD_OP")
    )


def test_a_list_item_needs_an_example_that_has_it_as_a_token_of_its_input() -> None:
    unit = ListUnit("U3", "Operators", ("+", "-", "*"))
    shown = [
        Case("F1.AC1", ("U3",), "`1 + 2`", "`3`"),
        Case("F1.AC2", ("U9",), "`1 * 2`", "`2`"),
        Case("F1.AC3", ("U3",), "`2024-01-05`", "`d`"),
    ]

    # `*` is shown by a case that cites another unit, `-` only inside a date.
    assert owed.untested_items(unit, shown) == ("-", "*")
    assert owed.carries_item(shown[0], unit, "+")


def test_a_prefix_operator_item_needs_an_example_whose_input_begins_with_it() -> None:
    unit = ListUnit("U3", "Operators", ("!", "&&"))
    after = Case("F1.AC1", ("U3",), "`a && !b`", "`x`")
    begins = Case("F1.AC2", ("U3",), "`!a`", "`x`")
    quoted = Case("F1.AC3", ("U3",), "`run --expr '!a'`", "`x`")

    assert not owed.carries_item(after, unit, "!")
    assert owed.carries_item(after, unit, "&&")
    assert owed.carries_item(begins, unit, "!")
    assert owed.carries_item(quoted, unit, "!")
    assert owed.untested_items(unit, [after]) == ("!",)
    assert owed.untested_items(unit, [after, begins]) == ()
    (found,) = owed.findings(
        Owed(lists=(unit,)), reply(criterion("F1.AC1", "U3", ("`a && !b`", "`x`")))
    )
    assert "`!` in its input" in found.text
    assert "prefix operator needs an example in which it begins the input" in found.text


def test_findings_name_each_unit_item_and_section_the_definition_still_owes() -> None:
    due = Owed(
        owed.arrows(UNITS)[0],
        owed.lists(UNITS),
        (Section("G2", ("C5", "C6"), "an expected block holds a placeholder"),),
        owed.arrows(UNITS)[1],
    )

    found = owed.findings(due, reply())

    assert [(item.kind, item.advisory) for item in found] == [
        (Kind.PROBE_ARROW, False),
        (Kind.PROBE_ARROW, False),
        (Kind.PROBE_UNDERIVABLE, True),
        (Kind.PROBE_UNDERIVABLE, True),
        (Kind.PROBE_LIST, False),
        (Kind.PROBE_UNDERIVABLE, False),
    ]
    assert "`U2` states an outcome with an arrow" in found[0].text
    assert "`div` in its input and `ZERO`" in found[1].text
    assert "`U3` lists 3 items (Operators)" in found[4].text
    assert "`+`, `-`, `*`" in found[4].text
    assert "G2 (C5 to C6)" in found[5].text


def test_a_definition_that_answers_each_obligation_owes_nothing_blocking() -> None:
    due = Owed(
        owed.arrows(UNITS)[0],
        owed.lists(UNITS),
        (Section("G2", ("C5", "C6"), "why"),),
    )
    body = reply(
        criterion("F1.AC1", "U2", ("`wat`", "`BAD_OP`")),
        criterion("F1.AC2", "U4", ("`div 1 0`", "`ZERO`")),
        criterion("F1.AC3", "U3", ("`1 + 1`", "`2`"), ("`1 - 1`", "`0`"), ("`2 * 2`", "`4`")),
        criterion("F1.AC4", "C6", ("`x`", "`y`")),
    )

    assert owed.findings(due, body) == ()


def test_a_long_list_of_missing_items_is_cut_short() -> None:
    unit = ListUnit("U1", "Flags", tuple(f"-{letter}" for letter in "abcdefghij"))

    (found,) = owed.findings(Owed(lists=(unit,)), reply())

    assert "and 2 more" in found.text


def test_an_owed_list_survives_the_context_file_it_travels_in() -> None:
    due = Owed(
        (Arrow("U2", None, "BAD_OP"), Arrow("U4", "div", "ZERO")),
        (ListUnit("U3", "Operators", ("+", "-")),),
        (Section("G1", ("C1",), "why"),),
        (Loose("U5", "reason"),),
    )

    assert Owed.read(due.record()) == due
    assert Owed.read({}) == Owed()


def owed_by(units: compact.Units) -> Owed:
    """What an issue without example sections obliges a definition to give examples for."""
    good, loose = owed.arrows(units)
    return Owed(good, owed.lists(units), (), loose)


FIRST = """\
title: Calculator
objective: Calculate.
feature F1: Calculator
outcome: It calculates.
ac F1.AC1: It rejects what it does not know and divides.
source: U1 U2 U3 U4 U5 U6
aspect: an unknown operator -> the program prints `BAD_OP`
example: `wat` -> `BAD_OP`
"""
REST = """\
example: `div 1 0` -> `ZERO`
example: `1 + 1` with `{}` -> `2`
example: `1 - 1` -> `0`
example: `2 * 2` -> `4`
"""


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    due = owed_by(UNITS)
    tool.start(
        directory,
        tool.Context(ISSUE, (), "(first round)", (), str(tmp_path), 2, due),
    )
    return directory


def call(state: Path, *args: str, stdin: str = "") -> tool.Outcome:
    return tool.run(state, list(args), lambda: stdin)


def test_the_context_carries_what_the_definition_owes_through_the_state_file(
    state: Path,
) -> None:
    held = json.loads((state / tool.CONTEXT_FILE).read_text(encoding="utf-8"))

    read = tool.Context.read(held)

    assert read.owed is not None
    assert [arrow.id for arrow in read.owed.arrows] == ["U2", "U4"]
    assert tool.Context("", (), "", (), "", 1).record()["probes_owed"] is None
    assert tool.Context.read({**held, "probes_owed": None}).owed is None


def test_done_refuses_until_each_arrow_and_list_item_has_an_example(
    state: Path,
) -> None:
    put = call(state, "put", stdin=FIRST)
    refused = call(state, "done", "ready")

    assert refused.code == 1
    assert "not sealed" in refused.output
    assert "`U4` states an outcome with an arrow" in refused.output
    assert "`U3` lists 3 items (Operators)" in refused.output
    kinds = set(refused.call.kinds)
    assert {Kind.PROBE_ARROW, Kind.PROBE_LIST} <= kinds
    # The arrow with no outcome literal is told to the intake, not demanded of it.
    assert "(advisory)" in put.output
    assert Kind.PROBE_UNDERIVABLE in put.call.kinds

    call(state, "put", stdin=FIRST + REST)
    sealed = call(state, "done", "ready")

    assert sealed.code == 0, sealed.output
    assert sealed.output.startswith("done ready: sealed")


def test_a_workflow_without_probes_owes_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "plain"
    tool.start(directory, tool.Context(ISSUE, (), "(first round)", (), str(tmp_path), 2))
    call(directory, "put", stdin=FIRST)

    sealed = call(directory, "done", "ready")

    assert sealed.code == 0
    assert Kind.PROBE_ARROW not in sealed.call.kinds


def test_only_a_ready_reply_owes_examples(state: Path) -> None:
    call(state, "put", stdin=FIRST)
    draft = tool.read_draft(state)
    assert draft is not None
    context = tool.Context.read(json.loads((state / tool.CONTEXT_FILE).read_text(encoding="utf-8")))
    units = compact.units(ISSUE, [], V2)
    asking = draft.reply(SEALS["ask"])

    checked = check_reply(asking, units, ISSUE, Path(context.workdir), ())
    owing = with_probes(checked, asking, context.owed)

    assert owing.owed == ()
    assert with_probes(checked, draft.reply(SEALS["ready"]), None).owed == ()
    ready = draft.reply(SEALS["ready"])
    assert with_probes(check_reply(ready, units, ISSUE, None, ()), ready, Owed()).owed == ()
