"""The block grammar of ``define put``: what a line may hold, and what each fault says."""

from __future__ import annotations

import pytest

from raidar.subject.workflow.define_tool import block
from raidar.subject.workflow.define_tool.block import Item
from raidar.subject.workflow.define_tool.kinds import Kind

FULL = """\
title: Greeting endpoint
objective: Greet a caller by name.
named: F1 Greeting
from: issue U2
named: Export
from: code src/export.py:10-20
feature F1: Greeting
outcome: GET /greet answers
risk: requires review
ac F1.AC1: Greets Ada.
source: U2 U1
source: U3
aspect: GET /greet with `name=Ada` -> the reply is `hello Ada`
example: GET /greet with `name=Ada` -> `hello Ada`
ac F1.AC2: Earlier behaviour still works.
kind: regression
decision 2: 405 is the refusal
rung: 3
source: U3
ref: src/greet.py:4
ref: README.md:1
reverse: by returning 404
risk: requires review
retire: tests/test_x.py::test_old
source: U3
reason: the refusal changes
question F1 Greeting: What is it for?
kind: objective_unclear
proposal: Liveness only.
option: Readiness too.
option: Both.
reply Q1.1: overridden
quote: Q1.1: b
"""


def only(block_text: str) -> list[Item]:
    parsed = block.parse(block_text)
    assert [item.text for item in parsed.findings] == []
    return list(parsed.items)


def findings(block_text: str) -> list[str]:
    return [item.text for item in block.parse(block_text).findings]


def test_a_block_reads_into_the_items_of_the_reply() -> None:
    items = {(item.kind, item.key): item for item in only(FULL)}

    assert items[("title", "title")].data == {"text": "Greeting endpoint"}
    assert items[("named", "f1 greeting")].data == {
        "name": "F1 Greeting",
        "behaviour_from": "issue",
        "evidence": "U2",
    }
    assert items[("named", "export")].data["evidence"] == "src/export.py:10-20"
    assert items[("feature", "F1")].data == {
        "id": "F1",
        "name": "Greeting",
        "outcome": "GET /greet answers",
        "risk": "requires review",
    }
    first = items[("ac", "F1.AC1")]
    assert first.owner == "F1"
    assert first.data == {
        "id": "F1.AC1",
        "text": "Greets Ada.",
        "sources": ["U2 U1", "U3"],
        "kind": "behaviour",
        "aspects": [
            {
                "situation": "GET /greet with `name=Ada`",
                "outcome": "the reply is `hello Ada`",
            }
        ],
        "examples": [{"input": "GET /greet with `name=Ada`", "expected": "`hello Ada`"}],
    }
    assert items[("ac", "F1.AC2")].data["kind"] == "regression"
    assert items[("decision", "2")].data == {
        "text": "405 is the refusal",
        "rung": "3",
        "sources": ["U3"],
        "refs": ["src/greet.py:4", "README.md:1"],
        "reverse": "by returning 404",
        "risk": "requires review",
    }
    assert items[("retire", "tests/test_x.py::test_old")].data == {
        "test": "tests/test_x.py::test_old",
        "sources": ["U3"],
        "reason": "the refusal changes",
    }
    assert items[("question", "F1 Greeting objective_unclear")].data == {
        "feature": "F1 Greeting",
        "kind": "objective_unclear",
        "question": "What is it for?",
        "proposal": "Liveness only.",
        "options": ["Readiness too.", "Both."],
    }
    assert items[("reply", "Q1.1")].data == {
        "question": "Q1.1",
        "outcome": "overridden",
        "quote": "Q1.1: b",
    }


def test_what_show_writes_reads_back_as_the_same_items() -> None:
    items = only(FULL)

    assert only(block.show(items)) == items
    assert block.show([]) == ""


def test_a_value_is_taken_verbatim_to_the_end_of_the_line() -> None:
    awkward = '{"code": 1, "msg": "a: b"} ```py EOF \\n \\"q\\" `x -> y`'
    items = only(f"feature F1: f\nac F1.AC1: {awkward}\nsource: U1\naspect: a -> {awkward}\n")

    criterion = items[1]
    assert criterion.data["text"] == awkward
    assert criterion.data["aspects"] == [{"situation": "a", "outcome": awkward}]


@pytest.mark.parametrize(
    ("line", "pair"),
    [
        ("a -> b", ("a", "b")),
        ("`x -> y` -> `z`", ("`x -> y`", "`z`")),
        ("when `a -> b` -> then", ("when `a -> b`", "then")),
    ],
)
def test_the_arrow_that_splits_the_sides_is_the_one_outside_backticks(
    line: str, pair: tuple[str, str]
) -> None:
    items = only(f"feature F1: f\nac F1.AC1: t\naspect: {line}\nexample: {line}\n")

    data = items[1].data
    assert (data["aspects"][0]["situation"], data["aspects"][0]["outcome"]) == pair  # type: ignore[index]
    assert (data["examples"][0]["input"], data["examples"][0]["expected"]) == pair  # type: ignore[index]


@pytest.mark.parametrize("line", ["no arrow", "a -> b -> c", "`a -> b`"])
def test_a_side_pair_needs_exactly_one_arrow_outside_backticks(line: str) -> None:
    text = f"feature F1: f\nac F1.AC1: t\nsource: U1\naspect: {line}\n"

    (found,) = findings(text)

    assert found.startswith("F1.AC1 line 4: `aspect:` needs one `->` outside backticks")
    assert block.parse(text).findings[0].kind == Kind.GRAMMAR
    # The criterion is still built, without the line.
    assert block.parse(text).items[1].data["aspects"] == []


def test_indentation_and_blank_lines_do_not_matter() -> None:
    items = only("\n   title:   Spaces  \n\n\t objective:\tTabs\n")

    assert [item.data["text"] for item in items] == ["Spaces", "Tabs"]


def test_a_criterion_belongs_to_the_feature_its_id_names_else_the_one_before_it() -> None:
    items = only("feature F1: a\nac F2.AC1: named by id\nfeature F3: b\nac odd: by position\n")

    owners = {item.key: item.owner for item in items if item.kind == "ac"}
    assert owners == {"F2.AC1": "F2", "odd": "F3"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("just some words", ["line 1: `just some words` is not a `key: value` line"]),
        (": no key", ["line 1: `: no key` is not a `key: value` line"]),
        ("colour: red", ["line 1: unknown key `colour`"]),
        ("a b c: d", ["line 1: unknown key `a b c`"]),
        ("outcome: early", ["line 1: `outcome:` comes before the item it belongs to"]),
        (
            "feature: x",
            ["line 1: `feature` takes an id before the colon (`feature <id>: <text>`)"],
        ),
        (
            "ac a b: x",
            ["line 1: `ac` takes an id before the colon (`ac <id>: <text>`)"],
        ),
        (
            "title: t\noutcome: x",
            ["title line 2: `outcome:` is not a line of title"],
        ),
        (
            "feature F1: f\nsource: U1",
            ["F1 line 2: `source:` is not a line of feature; it takes `outcome`, `risk`"],
        ),
        ("title:", ["title line 1: has no text after the colon"]),
        ("ac F1.AC1:", ["F1.AC1 line 1: has no text after the colon"]),
    ],
)
def test_a_line_the_tool_cannot_read_is_named_by_its_number(text: str, expected: list[str]) -> None:
    assert findings(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "named: X",
            "named `X` line 1: needs a `from:` line (`issue U3 U4`, `code path:line`, "
            "`tests path:line` or `none`)",
        ),
        (
            "named: X\nfrom: wishful U1",
            "named `X` line 2: `from:` starts with `wishful`, not one of `issue` | "
            "`code` | `tests` | `none`",
        ),
        (
            "ac odd: t",
            "odd line 1: `odd` names no feature: write its id as `F<n>.AC<n>`, or put "
            "it after the `feature` line it belongs to",
        ),
        (
            "decision x: t",
            "decision x line 1: `x` is not a number: write `decision 1: <text>`",
        ),
        ("decision 1: t", "decision 1 line 1: needs a `rung:` line, `1` to `5`"),
        (
            "decision 1: t\nrung: 9",
            "decision 1 line 2: `rung:` is `9`, not one of `1` | `2` | `3` | `4` | `5`",
        ),
        ("question Q: t", "question Q line 1: needs a `kind:` line"),
        ("question Q: t\nkind: odd", "question Q line 1: needs a valid `kind:`"),
        (
            "reply Q1.1: maybe",
            "reply Q1.1 line 1: `maybe` is not one of `accepted` | `overridden` | "
            "`answered` | `unclear` | `unanswered`",
        ),
    ],
)
def test_an_item_missing_what_the_reply_requires_is_refused_with_the_reason(
    text: str, expected: str
) -> None:
    parsed = block.parse(text)

    assert parsed.items == ()
    assert expected in [item.text for item in parsed.findings]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "feature F1: f\nrisk: reckless",
            "F1 line 2: `risk:` is `reckless`, not one of `requires review`",
        ),
        (
            "feature F1: f\nac F1.AC1: t\nkind: documentation",
            "F1.AC1 line 3: `kind:` is `documentation`, not one of `behaviour` | `regression`",
        ),
    ],
)
def test_a_bad_optional_value_is_reported_and_the_item_is_kept_without_it(
    text: str, expected: str
) -> None:
    parsed = block.parse(text)

    assert [item.text for item in parsed.findings] == [expected]
    assert [item.kind for item in parsed.items][0] == "feature"
    assert parsed.items[-1].data.get("risk", "") == ""
    assert parsed.items[-1].data.get("kind", "behaviour") == "behaviour"


def test_an_item_given_twice_in_a_block_keeps_the_last_and_says_so() -> None:
    parsed = block.parse("feature F1: a\nfeature F1: b\ntitle: x\n")

    assert [item.text for item in parsed.findings] == [
        "`F1` is given 2 times in this block: the last one is kept"
    ]
    assert len(parsed.items) == 3


def test_a_question_may_name_its_feature_in_several_words() -> None:
    (item,) = only("question CSV export: Which columns?\nkind: missing_input\n")

    assert item.data["feature"] == "CSV export"
    assert item.ref == "question CSV export missing_input"


def test_an_item_names_itself_for_drop() -> None:
    refs = [item.ref for item in only(FULL)]

    assert refs == [
        "title",
        "objective",
        "named f1 greeting",
        "named export",
        "F1",
        "F1.AC1",
        "F1.AC2",
        "decision 2",
        "retire tests/test_x.py::test_old",
        "question F1 Greeting objective_unclear",
        "Q1.1",
    ]
