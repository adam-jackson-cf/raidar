"""The define stage under the inputs review found it mishandling.

Each test drives the scenario the review recorded: a section that repeats, a
required unit nothing can cite, an empty criterion, label lines inside a
wrapped criterion, a paragraph wrapped by indentation, placeholder
verifications, code fences of either kind, quotes that cut a word, unit ranges,
code comments that read as headings and a malformed id reported twice.
"""

from __future__ import annotations

from typing import Any

import pytest

from raidar.subject.workflow.define_tool import (
    compact,
    evidence,
    featuredef,
    normative,
    sections,
)
from raidar.subject.workflow.define_tool.core import JsonObject
from raidar.subject.workflow.define_tool.unitrules import V1

# The traced reading: each criterion has an id and a `Verification:` line.
TRACED_DEFINITION = """\
# Health endpoint

## Objective

Expose a health check so deploys can verify the service.

## Features

### F1 Health

Outcome: GET /health is 200

Acceptance criteria:
- F1.AC1: GET /health returns 200.
  Verification: `test_health.py::test_health` calls health() and asserts 200.
- F1.AC2: POST /health is refused.
  Verification: `test_health.py::test_health_rejects_post` asserts the refusal.

## Decisions and assumptions

- The body is empty (source: 5: default; reverse by returning a JSON body).
"""
SECOND_FEATURES = """
## Features

### F2 Ready

Outcome: GET /ready is 200

Acceptance criteria:
- F2.AC1: GET /ready returns 200.
  Verification: `test_health.py::test_ready` asserts 200.
"""


@pytest.mark.parametrize(
    ("text", "section"),
    [
        (TRACED_DEFINITION + SECOND_FEATURES, "Features"),
        (
            TRACED_DEFINITION.replace("## Features", "## objective\n\nAnother aim.\n\n## Features"),
            "Objective",
        ),
    ],
    ids=["features-repeated", "objective-repeated-in-another-case"],
)
def test_a_repeated_section_is_a_blocking_finding(text: str, section: str) -> None:
    found = featuredef.findings(text, traced=True)

    repeated = [item for item in found if "more than once" in item.text]
    assert [item.text for item in repeated] == [
        f"the `## {section}` section appears more than once: "
        "write each section once, with everything it holds under it"
    ]
    assert not repeated[0].advisory


def test_a_definition_whose_sections_appear_once_has_no_repeat_finding() -> None:
    assert featuredef.check(TRACED_DEFINITION, traced=True) == []


def with_criteria(*lines: str) -> str:
    """Return the traced definition with its criteria list replaced by ``lines``."""
    head, _, rest = TRACED_DEFINITION.partition("Acceptance criteria:\n")
    _, _, tail = rest.partition("\n## Decisions")
    return head + "Acceptance criteria:\n" + "\n".join(lines) + "\n\n## Decisions" + tail


# --- DEF-2: a criterion says something ------------------------------------------


def test_a_criterion_with_an_id_and_no_text_is_a_blocking_finding() -> None:
    text = with_criteria("- F1.AC1:", "  Verification: `t.py::test_a` asserts it.")

    found = featuredef.findings(text, traced=True)

    assert [item.text for item in found] == [
        "acceptance criterion `F1.AC1` has no text: write the observable criterion after its id"
    ]
    assert not found[0].advisory


def test_a_criterion_whose_text_wraps_onto_the_next_line_has_text() -> None:
    text = with_criteria(
        "- F1.AC1:",
        "  GET /health returns 200.",
        "  Verification: `t.py::t` asserts it.",
    )

    assert featuredef.findings(text, traced=True) == []


# --- DEF-3: a label line inside a wrapped criterion --------------------------------


def test_an_indented_label_under_a_criterion_is_its_wrapped_text() -> None:
    text = with_criteria(
        "- F1.AC1: GET /health returns 200 and",
        "  Outcome: the body is empty",
        "  Verification: `t.py::test_a` asserts it.",
        "- F1.AC2: POST /health is refused,",
        "  acceptance criteria from the issue are kept verbatim.",
        "  Verification: `t.py::test_b` asserts it.",
    )

    parsed = featuredef.parse(text, traced=True)

    (feature,) = parsed.features
    assert feature.outcome == "GET /health is 200"
    assert [item.id for item in feature.traced] == ["F1.AC1", "F1.AC2"]
    assert feature.traced[0].text == "GET /health returns 200 and Outcome: the body is empty"
    assert feature.traced[1].text.endswith("are kept verbatim.")


def test_a_phrase_that_only_starts_with_the_criteria_label_is_not_the_label() -> None:
    text = TRACED_DEFINITION.replace(
        "Acceptance criteria:\n",
        "Acceptance criteria from the issue:\n- F1.AC9: x.\nAcceptance criteria:\n",
    )

    (feature,) = featuredef.parse(text, traced=True).features

    assert [item.id for item in feature.traced] == ["F1.AC1", "F1.AC2"]


@pytest.mark.parametrize(
    "label",
    ["Acceptance criteria", "**Acceptance criteria**", "**Acceptance criteria:**"],
)
def test_the_criteria_label_alone_still_opens_the_list(label: str) -> None:
    text = TRACED_DEFINITION.replace("Acceptance criteria:", label)

    (feature,) = featuredef.parse(text, traced=True).features

    assert [item.id for item in feature.traced] == ["F1.AC1", "F1.AC2"]


def test_a_fully_indented_feature_block_still_reads_its_labels() -> None:
    text = TRACED_DEFINITION.replace("\nOutcome:", "\n  Outcome:").replace(
        "\nAcceptance criteria:\n- ", "\n  Acceptance criteria:\n  - "
    )

    (feature,) = featuredef.parse(text, traced=True).features

    assert feature.outcome == "GET /health is 200"
    assert [item.id for item in feature.traced] == ["F1.AC1", "F1.AC2"]


# --- DEF-5: a placeholder verification -------------------------------------------


@pytest.mark.parametrize("value", ["N/A.", "TBD.", "None!", "(none)", "**n/a**", "-", "- ."])
def test_a_placeholder_verification_with_punctuation_names_no_test(value: str) -> None:
    text = with_criteria("- F1.AC1: GET /health returns 200.", f"  Verification: {value}")

    found = featuredef.check(text, traced=True)

    assert found == [
        "acceptance criterion `F1.AC1` has no `Verification:` line: under it, "
        "name the automated test that will prove it"
    ]


# --- DEF-6: code fences of either kind ------------------------------------------


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "~~~~~"])
def test_a_heading_inside_a_fence_of_either_kind_is_code(fence: str) -> None:
    text = f"intro\n\n{fence}\n## Not a section\n### Not a feature\n{fence}\n\n## Real\nbody"

    head, found = sections.split(text)

    assert [name for name, _ in found] == ["Real"]
    assert "## Not a section" in head


def test_a_fence_closes_only_on_its_own_kind_and_at_least_its_length() -> None:
    fences = sections.Fences()

    reads = [fences.delimits(line) for line in ("````", "```", "~~~~", "``` x", "````", "plain")]

    assert reads == [True, False, False, False, True, False]
    assert not fences.inside


def test_a_backtick_run_with_a_backtick_after_it_is_a_code_span_not_a_fence() -> None:
    fences = sections.Fences()

    assert not fences.delimits("``` code ``` span")
    assert not fences.inside


def test_an_example_heading_in_a_fence_is_no_stray_heading() -> None:
    text = TRACED_DEFINITION.replace(
        "Outcome: GET /health is 200\n",
        "Outcome: GET /health is 200\n\n~~~\n### Example heading\n~~~\n",
    )

    parsed = featuredef.parse(text, traced=True)

    assert parsed.stray == ()
    assert [item.id for item in parsed.features] == ["F1"]


# --- DEF-4: an indented line wraps its paragraph ---------------------------------


def test_an_indented_line_under_a_paragraph_continues_one_sentence() -> None:
    issue = "The tool must accept a list\n  of names and return them sorted.\n"

    assert [unit.text for unit in normative.units(issue, V1)] == [
        "The tool must accept a list of names and return them sorted."
    ]


def test_an_indented_line_after_a_blank_line_starts_a_new_paragraph() -> None:
    issue = "The tool must accept a list.\n\n  Names are sorted.\n"

    assert [unit.text for unit in normative.units(issue, V1)] == [
        "The tool must accept a list.",
        "Names are sorted.",
    ]


# --- DEF-7: a quote starts and ends on whole words --------------------------------

SORT_ISSUE = "# Sorting\n\nSort the rows by name ascending. Ties keep their order.\n"


@pytest.mark.parametrize(
    "quote",
    [
        "ort the rows by name ascending",
        "Sort the rows by name ascend",
        "the rows by na",
    ],
)
def test_evidence_that_cuts_a_word_is_not_the_issues_words(quote: str) -> None:
    assert evidence.quote_problem("rows", quote, SORT_ISSUE) == (
        "the `from:` evidence is not a verbatim quote from the issue"
    )


def test_evidence_of_whole_words_still_holds() -> None:
    assert evidence.quote_problem("rows", "Sort the rows by name ascending.", SORT_ISSUE) == ""


# --- DEF-9: a code comment is not a heading ---------------------------------------

COMMENT_ISSUE = (
    "# Answers\n\nThe tool prints the answer:\n\n```\n# expected: 42\nresult = 42\n```\n"
)


def test_a_comment_line_in_a_fence_is_quotable_and_does_not_end_a_quote() -> None:
    assert evidence.verbatim_problem("# expected: 42", COMMENT_ISSUE, 1) == ""
    assert evidence.verbatim_problem("expected: 42 result = 42", COMMENT_ISSUE, 1) == ""


def test_a_heading_outside_a_fence_is_still_a_heading() -> None:
    assert evidence.verbatim_problem("Answers", COMMENT_ISSUE, 1) == "is only a heading"


def test_the_code_comment_is_a_unit_a_reply_can_cite() -> None:
    found = compact.units(COMMENT_ISSUE, (), V1)

    comment = next(unit for unit in found.items if unit.text == "# expected: 42")
    assert comment.id.startswith("C")
    assert comment.citable


# --- DEF-1: a required unit can be cited -------------------------------------------

SHORT_ISSUE = "# Pager\n\nThe tool lists names.\n\n- Sorted.\n- Names are sorted by length.\n"


def test_a_normative_fragment_too_short_to_quote_is_not_required() -> None:
    found = compact.units(SHORT_ISSUE, (), V1)

    fragment = next(unit for unit in found.items if unit.text == "Sorted.")
    assert fragment.signals
    assert not fragment.required
    assert not fragment.citable
    assert "Sorted." not in found.text()
    assert [unit.text for unit in found.required()] == [
        "The tool lists names.",
        "Names are sorted by length.",
    ]


def test_a_normative_unit_failing_the_quote_check_for_another_reason_stays_required() -> None:
    # The hidden comment makes the unit differ from the issue's own words, so it
    # fails the verbatim check; it is long enough, so it stays required and listed.
    issue = "# P\n\nNames <!-- hidden --> are sorted by length.\n\n- Sorted.\n"

    found = compact.units(issue, (), V1)

    (long,) = [unit for unit in found.items if unit.text.endswith("by length.")]
    assert evidence.verbatim_problem(long.text, issue, featuredef.SOURCE_MIN_WORDS)
    assert (long.required, long.citable) == (True, True)
    assert [unit.id for unit in found.required()] == [long.id]


def first_feature(definition: JsonObject) -> dict[str, Any]:
    """The first feature of a reply's definition, to edit."""
    features = definition["features"]
    assert isinstance(features, list)
    feature = features[0]
    assert isinstance(feature, dict)
    return feature


def reply(*sources: str) -> JsonObject:
    return {
        "title": "Pager",
        "objective": "List names.",
        "features": [
            {
                "id": "F1",
                "name": "Listing",
                "outcome": "Names are listed.",
                "risk": "",
                "criteria": [
                    {
                        "id": "F1.AC1",
                        "text": "Names are listed in order.",
                        "sources": list(sources),
                        "kind": "behaviour",
                        "aspects": [
                            {
                                "situation": "`pager` gets names",
                                "outcome": "it lists `3` names",
                            }
                        ],
                        "examples": [],
                    }
                ],
            }
        ],
        "decisions": [],
        "retires": [],
    }


def test_a_reply_citing_every_required_unit_has_no_contradictory_repair() -> None:
    found = compact.units(SHORT_ISSUE, (), V1)
    required = [unit.id for unit in found.required()]

    checked = compact.check(reply(*required), found, SHORT_ISSUE)

    assert checked.uncovered == ()
    assert not [item for item in checked.findings if "Sorted." in item.text]


# --- DEF-8: a range cites every unit in it ----------------------------------------


@pytest.mark.parametrize(
    ("said", "ids"),
    [
        ("U3-U5", ("U3", "U4", "U5")),
        ("U3-5, C1", ("U3", "U4", "U5", "C1")),
        ("U3 \u2013 U4 and U4", ("U3", "U4")),
        ("U5-U3", ("U5", "U3")),
        ("U1-U9999", ("U1", "U9999")),
        ("U3-C5", ("U3", "C5")),
        ("U3-U3", ("U3",)),
    ],
)
def test_cited_expands_a_range_of_one_kind(said: str, ids: tuple[str, ...]) -> None:
    assert compact.cited(said) == ids


def test_a_range_in_sources_covers_the_middle_units() -> None:
    found = compact.units(
        "# T\n\nNames are sorted.\n\nThe order is stable.\n\nIt never fails.\n", (), V1
    )

    checked = compact.check(
        reply("U1-U3"),
        found,
        "# T\n\nNames are sorted.\n\nThe order is stable.\n\nIt never fails.\n",
    )

    assert checked.uncovered == ()


# --- DEF-10: a malformed id is reported once -------------------------------------


@pytest.mark.parametrize("ident", ["AC1", "", "F2.AC1"])
def test_a_malformed_criterion_id_is_reported_once(ident: str) -> None:
    definition = reply("U1")
    first_feature(definition)["criteria"][0]["id"] = ident
    found = compact.units(SHORT_ISSUE, (), V1)

    checked = compact.check(definition, found, SHORT_ISSUE)

    about = [item for item in checked.errors if "acceptance criterion" in item and "id" in item]
    assert len(about) == 1
    assert "has no id" not in about[0]


@pytest.mark.parametrize("ident", ["1", "F1x"])
def test_a_malformed_feature_id_is_reported_once(ident: str) -> None:
    definition = reply("U1")
    first_feature(definition)["id"] = ident

    checked = compact.check(definition, compact.units(SHORT_ISSUE, (), V1), SHORT_ISSUE)

    assert [item for item in checked.errors if "is not a `### F<n>" in item] == []
    assert [item for item in checked.errors if f"feature id `{ident}`" in item] == [
        f"feature id `{ident}` is not `F<n>`: number the features `F1`, `F2`, …"
    ]


def test_a_compact_criterion_without_text_is_blocking() -> None:
    definition = reply("U1")
    first_feature(definition)["criteria"][0]["text"] = ""

    checked = compact.check(definition, compact.units(SHORT_ISSUE, (), V1), SHORT_ISSUE)

    assert any("has no text" in item for item in checked.blocking)
