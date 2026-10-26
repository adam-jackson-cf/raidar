"""The compact intake's pure pieces: unit ids, the reply checks, the render, the evidence check."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from raidar.subject.workflow.define_tool import compact, featuredef
from raidar.subject.workflow.define_tool.core import JsonObject, rows
from raidar.subject.workflow.define_tool.kinds import Kind
from raidar.subject.workflow.define_tool.unitrules import V1, V2

ISSUE = """\
# Pager

The tool reads a list of names.

Add a `--top N` option that must reject N below 1 with exit code `2`.

It prints "done" when it has finished.

It prints:

```
a: 1
b: 2
```

Names are sorted. The order is stable.

Rules:
* `N` is a whole number
* Unknown flags are ignored

Some background without a requirement.
"""
UNITS = compact.units(ISSUE, ["answer: names may repeat"], V1)
# U1 reads, U2 --top, U3 prints "done", U4 It prints:, U5 sorted, U6 stable,
# U7 Rules:, U8 N whole, U9 flags, U10 background; C1 a: 1, C2 b: 2; A1.
READS, TOP, DONE, PRINTS, SORTED, STABLE, RULES, WHOLE, FLAGS = (f"U{n}" for n in range(1, 10))
REQUIRED = ["U1", "U2", "U3", "U5", "U6", "U8", "U9"]


def base() -> JsonObject:
    """Return a clean reply's ``definition`` for ``ISSUE``."""
    return {
        "title": "Pager",
        "objective": "List names in order.",
        "features": [
            {
                "id": "F1",
                "name": "Top option",
                "outcome": "A user limits the list.",
                "risk": "",
                "criteria": [
                    {
                        "id": "F1.AC1",
                        "text": "A top of 0 is rejected.",
                        "sources": [READS, TOP],
                        "kind": "behaviour",
                        "aspects": [
                            {
                                "situation": "`pager list.txt --top N` gets `N` of `0`",
                                "outcome": "the command exits with code `2`",
                            }
                        ],
                        "examples": [{"input": "`--top 0`", "expected": "exit `2`"}],
                    },
                    {
                        "id": "F1.AC2",
                        "text": "Earlier behaviour still works.",
                        "sources": [SORTED],
                        "kind": "regression",
                        "aspects": [],
                        "examples": [],
                    },
                ],
            }
        ],
        "decisions": [],
        "retires": [],
    }


def edited(change: object, *path: str | int) -> JsonObject:
    """Return ``base()`` with the value at ``path`` replaced."""
    definition = base()
    target: Any = definition
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = change
    return definition


def feature(definition: JsonObject) -> JsonObject:
    return rows(definition, "features")[0]


def crit(definition: JsonObject, number: int = 0) -> JsonObject:
    return rows(feature(definition), "criteria")[number]


def run(definition: JsonObject) -> compact.Checked:
    return compact.check(definition, UNITS, ISSUE)


def covering(definition: JsonObject, *ids: str) -> JsonObject:
    """Return ``definition`` with its first criterion citing every id in ``ids``."""
    definition = copy.deepcopy(definition)
    crit(definition)["sources"] = list(ids)
    return definition


def blocking(definition: JsonObject) -> tuple[str, ...]:
    return run(covering(definition, *REQUIRED)).blocking


def test_units_are_numbered_in_document_order() -> None:
    assert UNITS.text().splitlines() == [
        "U1*: The tool reads a list of names.",
        "U2*: Add a `--top N` option that must reject N below 1 with exit code `2`.",
        'U3*: It prints "done" when it has finished.',
        "U5*: Names are sorted.",
        "U6*: The order is stable.",
        "U8*: `N` is a whole number",
        "U9*: Unknown flags are ignored",
        "U10: Some background without a requirement.",
        "C2: b: 2",
        "A1: answer: names may repeat",
    ]
    # A fragment that could not stand as a `Source:` quote keeps its number but is
    # not offered to the intake to cite: it would only earn a blocking finding.
    unlisted = [u.id for u in UNITS.items if not u.citable]
    assert unlisted == ["U4", "U7", "C1"]
    assert [u.id for u in UNITS.required()] == REQUIRED
    assert UNITS.get("U2") is not None
    top = UNITS.get("U2")
    assert top is not None
    assert top.signals[:1] == ("keyword",)
    assert UNITS.get("U99") is None


def test_the_required_units_are_the_normative_statements() -> None:
    from raidar.subject.workflow.define_tool import normative

    assert [u.text for u in UNITS.required()] == [u.text for u in normative.normative(ISSUE, V1)]


def test_cited_finds_each_id_once_in_order() -> None:
    assert compact.cited("U3, C1 and U3; A2 (F1.A1) U03 u4 XU5") == ("U3", "C1", "A2")
    assert compact.cited("none") == ()


def test_a_clean_reply_renders_the_definition_file_shape() -> None:
    definition = covering(base(), *REQUIRED)
    definition["decisions"] = [
        {
            "text": "Use the default sort",
            "rung": "2",
            "sources": [SORTED],
            "refs": ["pager.py:3"],
            "reverse": "sort by length",
            "risk": "",
        },
        {
            "text": "Keep\n   going\\n- not a bullet",
            "rung": "4",
            "sources": [],
            "refs": [],
            "reverse": "",
            "risk": "requires review",
        },
    ]
    definition["retires"] = [
        {
            "test": "tests/test_pager.py::test_old",
            "sources": [DONE],
            "reason": "the issue now says\nit prints",
        }
    ]
    feature(definition)["risk"] = "requires review"
    checked = run(definition)
    assert checked.blocking == ()
    assert checked.notes == ()
    assert checked.retired == ("tests/test_pager.py::test_old",)
    assert checked.rendered == (
        "# Pager\n\n"
        "## Objective\n\n"
        "List names in order.\n\n"
        "## Features\n\n"
        "### F1 Top option\n\n"
        "Outcome: A user limits the list.\n"
        "Risk: requires review\n\n"
        "Acceptance criteria:\n"
        "- F1.AC1: A top of 0 is rejected.\n"
        '  Source: "The tool reads a list of names."\n'
        '  Source: "Add a `--top N` option that must reject N below 1 with exit code `2`."\n'
        '  Source: "It prints "done" when it has finished."\n'
        '  Source: "Names are sorted."\n'
        '  Source: "The order is stable."\n'
        '  Source: "`N` is a whole number"\n'
        '  Source: "Unknown flags are ignored"\n'
        "  Aspect: `pager list.txt --top N` gets `N` of `0` -> the command exits with code `2`\n"
        "  Example: `--top 0` -> exit `2`\n"
        f"  Verification: {compact.VERIFICATION}\n"
        "- F1.AC2: Earlier behaviour still works.\n"
        '  Source: "Names are sorted."\n'
        "  Kind: regression\n"
        f"  Verification: {compact.REGRESSION_VERIFICATION}\n\n"
        "## Decisions and assumptions\n\n"
        "- Use the default sort; to reverse: sort by length "
        '(source: rung 2: "Names are sorted."; `pager.py:3`)\n'
        "- Keep going\\n- not a bullet (source: rung 4: `default`) requires review\n"
        "- retires `tests/test_pager.py::test_old`: the issue now says it prints "
        '(source: rung 2: "It prints "done" when it has finished.") requires review\n'
    )
    # The reader sees the same definition.
    parsed = featuredef.parse(checked.rendered, True, True, True)
    assert [c.id for c in parsed.acceptance()] == ["F1.AC1", "F1.AC2"]
    assert parsed.acceptance()[0].sources[2] == 'It prints "done" when it has finished.'
    assert len(parsed.decisions) == 3
    assert parsed.requires_review() == [parsed.decisions[1], parsed.decisions[2]]


def test_render_works_for_any_reply_the_schema_admits() -> None:
    empty: JsonObject = {
        "title": "",
        "objective": "",
        "features": [],
        "decisions": [],
        "retires": [],
    }
    assert compact.render(empty, UNITS).endswith("## Decisions and assumptions\n\n- None.\n")
    assert compact.render({}, UNITS) == compact.render(empty, UNITS)
    odd = base()
    crit(odd)["sources"] = ["U99", "see U1 and U2"]
    odd["decisions"] = [{"text": "x", "rung": "3", "sources": ["U99"], "refs": [" "]}]
    rendered = compact.render(odd, UNITS)
    assert "U99" not in rendered
    assert "- x (source: rung 3: `default`)" in rendered
    assert rendered.count("  Source:") == 3  # U1, U2 and the regression's U5


def test_a_reply_that_cites_everything_has_nothing_blocking_or_uncovered() -> None:
    checked = run(covering(base(), *REQUIRED))
    assert (checked.findings, checked.notes, checked.uncovered) == ((), (), ())
    assert checked.errors == checked.kinds == checked.blocking == ()


def test_a_required_unit_no_criterion_cites_is_advisory_and_listed() -> None:
    checked = run(base())
    assert [u.id for u in checked.uncovered] == ["U3", "U6", "U8", "U9"]
    assert checked.blocking == ()
    assert set(checked.kinds) == {Kind.NORMATIVE_UNDISPOSED}
    assert checked.errors[0] == compact.uncovered_error(checked.uncovered[0])
    assert 'U3 "It prints "done" when it has finished."' in checked.errors[0]
    assert "(conditional, behaviour)" in checked.errors[0]
    assert "add U3 to the `source:` line" in checked.errors[0]


def test_a_value_the_aspects_leave_out_is_a_note_never_sent_to_the_model() -> None:
    definition = covering(base(), *REQUIRED)
    rows(crit(definition), "aspects")[0]["outcome"] = "the command fails"
    crit(definition)["examples"] = [{"input": "`--top 0`", "expected": "exit `2`"}]
    checked = run(definition)
    assert checked.errors == ()
    assert [n.kind for n in checked.notes] == []
    crit(definition)["examples"] = [{"input": "`--top 0`", "expected": "exit `5`"}]
    rows(crit(definition), "aspects")[0]["situation"] = "`pager` gets a top"
    checked = run(definition)
    assert checked.errors == ()
    assert {n.kind for n in checked.notes} == {Kind.VALUE_LITERAL}
    assert all(n.advisory for n in checked.notes)


@pytest.mark.parametrize(
    ("definition", "kind", "words"),
    [
        (
            edited("X1", "features", 0, "id"),
            Kind.STRUCTURE,
            "feature id `X1` is not `F<n>`",
        ),
        (
            edited("F2.AC1", "features", 0, "criteria", 1, "id"),
            Kind.STRUCTURE,
            "criterion id `F2.AC1` under feature `F1` is not `F1.AC<n>`",
        ),
        (
            edited([], "features", 0, "criteria", 0, "sources"),
            Kind.QUOTE_MISSING,
            "acceptance criterion `F1.AC1` has no `source:` line",
        ),
        (
            edited(["U99"], "features", 0, "criteria", 0, "sources"),
            Kind.QUOTE_VERBATIM,
            "the `source:` line of acceptance criterion `F1.AC1` cites `U99`, which is no unit",
        ),
        (
            edited([PRINTS], "features", 0, "criteria", 0, "sources"),
            Kind.QUOTE_VERBATIM,
            "the unit U4 cited by acceptance criterion `F1.AC1` is too short",
        ),
        (
            edited(["C1"], "features", 0, "criteria", 0, "sources"),
            Kind.QUOTE_VERBATIM,
            "the unit C1 cited by acceptance criterion `F1.AC1` is too short",
        ),
        (
            edited(
                [
                    {
                        "text": "x",
                        "rung": "2",
                        "sources": ["U98"],
                        "refs": [],
                        "reverse": "",
                        "risk": "",
                    }
                ],
                "decisions",
            ),
            Kind.QUOTE_VERBATIM,
            "the `source:` line of decision 1 cites `U98`, which is no unit",
        ),
    ],
)
def test_each_reply_fault_is_blocking_with_its_kind(
    definition: JsonObject, kind: str, words: str
) -> None:
    # Cite every required unit, so a fault is never hidden by the coverage notes.
    checked = run(definition)
    hits = [
        (found.kind, found.text)
        for found in checked.findings
        if words in found.text and not found.advisory
    ]
    assert hits, checked.errors
    assert hits[0][0] == kind


def test_a_unit_from_an_earlier_answer_is_citable_without_the_quote_check() -> None:
    definition = edited(["A1"], "features", 0, "criteria", 0, "sources")
    assert not any("A1" in found.text for found in run(definition).findings)


def test_repeated_ids_are_reported_once() -> None:
    definition = base()
    definition["features"] = [feature(definition), copy.deepcopy(feature(definition))]
    findings = run(definition).blocking
    assert sum("feature id `F1` is used more than once" in f for f in findings) == 1
    assert sum("criterion id `F1.AC1` is used more than once" in f for f in findings) == 1
    assert sum("F1.AC2` is used more than once" in f for f in findings) == 1


def test_the_v10_rules_apply_to_what_the_file_says() -> None:
    no_arrow = edited("the command exits", "features", 0, "criteria", 0, "aspects", 0, "outcome")
    rows(crit(no_arrow), "aspects")[0]["situation"] = "a -> b"
    checked = run(no_arrow)
    assert any("`->`" in e for e in checked.blocking)
    assert Kind.ASPECT_SHAPE in checked.kinds
    regression = edited(
        [{"situation": "s", "outcome": "o"}], "features", 0, "criteria", 1, "aspects"
    )
    assert any("is `kind: regression` and so has no" in e for e in run(regression).blocking)
    bare = edited([], "features", 0, "criteria", 0, "aspects")
    assert any("has no `aspect:` line" in e for e in run(bare).blocking)
    unnamed = edited("", "features", 0, "name")
    assert any("has no name" in e for e in run(unnamed).blocking)


def test_an_aspect_that_quotes_an_id_fault_s_words_still_reports_its_own_fault() -> None:
    # `featuredef` words its id faults from the start of the message; an aspect
    # that happens to say the same must not drop the arrow fault it quotes.
    definition = edited(
        "a name that is used more than once, under feature `F1`: no",
        "features",
        0,
        "criteria",
        0,
        "aspects",
        0,
        "situation",
    )
    rows(crit(definition), "aspects")[0]["outcome"] = ""
    checked = run(covering(definition, *REQUIRED))
    assert any("has an empty side of its `->`" in e for e in checked.blocking)
    assert Kind.ASPECT_SHAPE in checked.kinds


def test_spaces_inside_a_literal_are_kept_and_only_line_breaks_are_joined() -> None:
    definition = edited(
        [{"input": "`greet  Ada `\n  today", "expected": "`hello  Ada `"}],
        "features",
        0,
        "criteria",
        0,
        "examples",
    )
    checked = run(covering(definition, *REQUIRED))
    assert "  Example: `greet  Ada ` today -> `hello  Ada `\n" in checked.rendered
    assert not [e for e in checked.blocking if "reads back" in e]
    parsed = featuredef.parse(checked.rendered, True, True, True)
    (example,) = parsed.acceptance()[0].examples
    assert (example.given, example.expected) == (
        "`greet  Ada ` today",
        "`hello  Ada `",
    )


def decided(text: str) -> JsonObject:
    return {
        "text": text,
        "rung": "3",
        "sources": [],
        "refs": [],
        "reverse": "",
        "risk": "",
    }


@pytest.mark.parametrize(
    ("definition", "words"),
    [
        (
            edited("## Features", "objective"),
            "the text of the objective reads back as ``",
        ),
        (
            edited("- a name", "features", 0, "name"),
            "the name of feature `F1` reads back as `a name`",
        ),
        (
            {**base(), "decisions": [decided("fine"), decided("")]},
            "the text of decision 2 reads back as `(source: rung 3: `default`)`",
        ),
    ],
)
def test_a_field_that_does_not_read_back_is_a_structure_finding(
    definition: JsonObject, words: str
) -> None:
    findings = [
        found
        for found in run(covering(definition, *REQUIRED)).findings
        if "reads back" in found.text
    ]
    assert len(findings) == 1
    assert findings[0].kind == Kind.STRUCTURE
    assert not findings[0].advisory
    assert words in findings[0].text
    assert findings[0].text.endswith("the text starts with markdown or a label; reword it")


def test_a_feature_or_criterion_another_finding_names_is_not_reported_for_reading_back() -> None:
    """Every criterion line starts with its label, so only a feature, the objective or a decision
    can fail to read back."""
    definition = edited("- a name", "features", 0, "name")
    reads = [f for f in run(covering(definition, *REQUIRED)).blocking if "reads back" in f]
    assert len(reads) == 1
    # Naming the feature in another finding leaves out the feature and its criteria.
    crit(definition, 1)["id"] = "F2.AC1"
    reads = [f for f in run(covering(definition, *REQUIRED)).blocking if "reads back" in f]
    assert reads == []
    # A finding that names only the criterion leaves out that criterion.
    definition = edited([], "features", 0, "criteria", 0, "aspects")
    findings = run(covering(definition, *REQUIRED)).blocking
    assert any("has no `aspect:` line" in f for f in findings)
    assert not any("reads back" in f for f in findings)


def test_retirements_are_granted_from_the_structured_list() -> None:
    definition = covering(base(), *REQUIRED)
    quoted = {
        "test": "tests/test_a.py::test_b",
        "sources": [DONE],
        "reason": "it prints",
    }
    definition["retires"] = [quoted, copy.deepcopy(quoted)]
    checked = run(definition)
    # The unit holds quote marks, so the rendered bullet's quote is not one the
    # file reader grants; the structured list is.
    assert checked.blocking == ()
    assert checked.retired == ("tests/test_a.py::test_b",)
    assert (
        featuredef.retired_tests(
            featuredef.parse(checked.rendered, True, True, True).decisions, ISSUE
        )
        == []
    )


@pytest.mark.parametrize(
    ("entry", "words"),
    [
        ({"test": "test_b", "sources": [SORTED], "reason": "r"}, "is not a test id"),
        (
            {"test": "tests/a.py::b", "sources": [], "reason": "r"},
            "has no `source:` line",
        ),
        (
            {"test": "tests/a.py::b", "sources": ["U98"], "reason": "r"},
            "which is no unit",
        ),
        ({"test": "tests/a.py::b", "sources": [PRINTS], "reason": "r"}, "is too short"),
    ],
)
def test_a_faulty_retirement_is_blocking_and_not_granted(entry: JsonObject, words: str) -> None:
    definition = covering(base(), *REQUIRED)
    definition["retires"] = [entry]
    checked = run(definition)
    assert checked.retired == ()
    assert any(words in e and "retirement" in e for e in checked.blocking)


def test_evidence_of_a_named_feature_is_unit_ids() -> None:
    def check(evidence: str, behaviour: str = "issue") -> str:
        feature: JsonObject = {
            "name": "listing",
            "behaviour_from": behaviour,
            "evidence": evidence,
        }
        return compact.feature_check(feature, UNITS, ISSUE, None, ["listing"])

    assert check("U1") == "ok"
    assert check("U6, U1") == "ok"
    assert check("U6").startswith("the `from:` evidence is only a heading, the name or a passing")
    assert check("U1 U99") == (
        "the `from:` evidence cites `U99`, which is no unit: cite ids from the numbered list only"
    )
    assert check("the tool reads") == (
        "the `from:` evidence names no unit id: cite the units (`U3`) that say what it does"
    )
    assert check("", behaviour="none") == "none"
    assert check("pager.py:3", behaviour="code") == "ok"
    assert check("pager.py", behaviour="tests") == "the `from:` evidence is not `path:line`"


def test_a_named_feature_is_recorded_with_the_units_it_cites() -> None:
    feature: JsonObject = {
        "name": "listing",
        "behaviour_from": "issue",
        "evidence": "U1 and U6",
    }
    record = compact.feature_record(feature, UNITS, ISSUE, None, ["listing"])
    assert record == {
        "name": "listing",
        "behaviour_from": "issue",
        "evidence": "U1 and U6",
        "evidence_check": "ok",
        "evidence_units": ["The tool reads a list of names.", "The order is stable."],
    }
    other: JsonObject = {"name": "x", "behaviour_from": "none", "evidence": "U5"}
    record = compact.feature_record(other, UNITS, ISSUE, None, ["x"])
    assert (record["evidence_check"], record["evidence_units"]) == ("none", [])


# --- the second unit rules ----------------------------------------------------------------

LISTED_ISSUE = ISSUE + ("\n* Marks: `!`, `*`, `&&`\n\nUnknown flags → `BAD_FLAG` error\n")
SECOND = compact.units(LISTED_ISSUE, (), V2)


def test_a_labelled_list_is_a_required_citable_unit_however_few_words_it_has() -> None:
    first = compact.units(LISTED_ISSUE, (), V1)
    listed = next(u for u in SECOND.items if u.text.startswith("Marks:"))

    assert not next(u for u in first.items if u.text.startswith("Marks:")).required
    assert listed.required
    assert listed.citable
    assert "enumeration" in listed.signals
    assert listed.id in [u.id for u in SECOND.required()]
    assert f"{listed.id}*: Marks: `!`, `*`, `&&`" in SECOND.text()
    assert SECOND.rules is V2


def test_an_arrow_outcome_is_required_under_the_second_unit_rules() -> None:
    arrow = next(u for u in SECOND.items if "BAD_FLAG" in u.text)

    assert arrow.required
    assert arrow.signals[-1] == "outcome"
    assert not next(
        u for u in compact.units(LISTED_ISSUE, (), V1).items if "BAD_FLAG" in u.text
    ).required


def second_cites(*extra: str) -> compact.Checked:
    return compact.check(covering(base(), *REQUIRED, *extra), SECOND, LISTED_ISSUE)


def test_an_uncited_required_unit_blocks_under_the_second_rules_only() -> None:
    flags = next(u.id for u in SECOND.items if u.text.startswith("Marks:"))
    arrow = next(u.id for u in SECOND.items if "BAD_FLAG" in u.text)

    held = second_cites()
    first = compact.check(
        covering(base(), *REQUIRED), compact.units(LISTED_ISSUE, (), V1), LISTED_ISSUE
    )

    assert {u.id for u in held.uncovered} == {flags, arrow}
    assert len(held.blocking) == 2
    assert all(not f.advisory for f in held.findings)
    assert first.blocking == ()
    assert all(f.advisory and f.kind == Kind.NORMATIVE_UNDISPOSED for f in first.findings)
    assert second_cites(flags, arrow).blocking == ()


def test_a_cited_labelled_list_passes_the_quote_check_whatever_its_word_count() -> None:
    flags = next(u.id for u in SECOND.items if u.text.startswith("Marks:"))
    arrow = next(u.id for u in SECOND.items if "BAD_FLAG" in u.text)

    found = second_cites(flags, arrow)

    assert found.blocking == ()
    assert 'Source: "Marks: `!`, `*`, `&&`"' in found.rendered


# A line that signals a requirement ("execute" is a behaviour verb) yet could not stand as
# a quote: its words first occur in a heading, so a quote of it is only that heading.
UNQUOTABLE_ISSUE = (
    "# Server\n\n### GET `/v1/execute`\n\nIt must reject N below 1 with exit code "
    "`2`.\n\n### 2) Run it\n\n**GET** `/v1/execute`\n\n```json\n{}\n```\n\n"
    "Marks: `!`, `*`\n"
)


def test_a_required_unit_that_cannot_be_cited_never_blocks_define_done() -> None:
    first = compact.units(UNQUOTABLE_ISSUE, (), V1)
    second = compact.units(UNQUOTABLE_ISSUE, (), V2)
    path = next(u for u in first.items if u.text.startswith("**GET**"))

    # Under the first rules it stays required and advisory (as it was measured).
    assert path.required
    assert second.get(path.id) is not None
    assert not second.get(path.id).required  # type: ignore[union-attr]
    assert not second.get(path.id).citable  # type: ignore[union-attr]
    # Every required unit of the second rules can be cited without a finding, and
    # citing nothing else of it leaves nothing that blocks except the units it names.
    for unit in second.required():
        cited = compact._unit_findings(
            "criterion 1", {"sources": [unit.id]}, second, UNQUOTABLE_ISSUE, 2
        )
        assert cited == [], unit.text


def test_the_second_rules_ask_for_no_unit_that_a_citation_would_be_refused() -> None:
    second = compact.units(UNQUOTABLE_ISSUE, (), V2)
    needed = sorted(u.id for u in second.required())
    reply = edited(needed, "features", 0, "criteria", 1, "sources")

    sealed = compact.check(covering(reply, *needed), second, UNQUOTABLE_ISSUE)

    assert sealed.uncovered == ()
    assert sealed.blocking == ()
