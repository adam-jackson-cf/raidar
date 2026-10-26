"""The intake's reply replayed on six recorded feature definitions.

Fixtures (``tests/fixtures/compact_replay``): the issue of each of the three
checkpoints (identical in both cohorts) and, per cohort (``a``, ``b``), the final
``feature-definition.md`` recorded for it. Each recorded definition is converted
back mechanically into the reply that says the same thing (each ``Source:``
quote becomes the unit ids it overlaps), checked and rendered, and the rendered
file must read back, for everything the judge reads, exactly as the recorded one.
This proves the round trip and the unit numbering; whether a model cites units
as well as it quotes them only a run shows. The sizes printed are bytes of
JSON against bytes of markdown, not tokens.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from raidar.subject.workflow.define_tool import compact, featuredef, normative
from raidar.subject.workflow.define_tool.core import rows, strings, text
from raidar.subject.workflow.define_tool.unitrules import V1
from tests.define_replay_support import reply

FIXTURES = Path(__file__).parent / "fixtures" / "define" / "compact_replay"
CASES = [(cohort, number) for cohort in ("a", "b") for number in (1, 2, 3)]
_RETIRES = re.compile(r"retires `([^`]+)`: (.*)")
BODY = {"features": [], "status": "ready", "questions": [], "replies": []}


def unquoted(issue: str, quotes: list[str]) -> list[normative.Unit]:
    """Return the issue's normative units that no quote shares a word-run with."""
    every = normative.units(issue, V1)
    touched = {index for quote in quotes for index in normative.sharing(quote, every)}
    return [unit for i, unit in enumerate(every) if unit.normative and i not in touched]


def read(cohort: str, number: int) -> tuple[str, str]:
    issue = (FIXTURES / f"issue-cp{number}.md").read_text()
    return issue, (FIXTURES / f"definition-{cohort}-cp{number}.md").read_text()


def judge_view(parsed: featuredef.FeatureDefinition) -> list[object]:
    """Return what the judge and the builder read of each criterion."""
    return [
        (
            item.id,
            item.text,
            item.kind,
            [(a.id, a.situation, a.outcome) for a in item.aspects],
            [(e.given, e.expected, e.literals) for e in item.examples],
        )
        for item in parsed.acceptance()
    ]


@pytest.mark.parametrize(("cohort", "number"), CASES)
def test_replay(cohort: str, number: int) -> None:
    issue, recorded_text = read(cohort, number)
    recorded = featuredef.parse(recorded_text, True, True, True)
    found = compact.units(issue, (), V1)
    definition = reply(recorded, found)

    # Every recorded criterion maps to at least one unit.
    cites = [
        item["sources"]
        for feature in rows(definition, "features")
        for item in rows(feature, "criteria")
    ]
    assert cites
    assert all(cites)

    checked = compact.check(definition, found, issue)
    assert checked.blocking == ()
    assert checked.retired == ()

    # What the judge reads, and the features around it, read back identically.
    rendered = featuredef.parse(checked.rendered, True, True, True)
    assert judge_view(rendered) == judge_view(recorded)
    assert [(f.id, f.name, f.outcome) for f in rendered.features] == [
        (f.id, f.name, f.outcome) for f in recorded.features
    ]

    # The normative statements the quotes covered are the units the criteria cite.
    left = {u.text for u in unquoted(issue, [q for c in recorded.acceptance() for q in c.sources])}
    quotes = [q for c in rendered.acceptance() for q in c.sources]
    assert {u.text for u in unquoted(issue, quotes)} == left
    assert {u.text for u in checked.uncovered} == left

    # Decisions survive, in order, each starting with its recorded text.
    heads = [item.rpartition(" (source:")[0] or item for item in recorded.decisions]
    assert len(rendered.decisions) == len(heads)
    for head, got in zip(heads, rendered.decisions, strict=True):
        assert got.startswith(head)
    # Retirements: the tests the recorded text part names. The recorded file
    # also granted a test its decision only says to keep (named after the
    # source); the rendered bullets, which hold the text part and the cited
    # units, do not.
    granted = featuredef.retired_tests(recorded.decisions, issue)
    kept = [t for t in granted if any(f"`{t}`" in head for head in heads)]
    assert featuredef.retired_tests(rendered.decisions, issue) == kept


def test_a_recorded_retirement_is_granted_from_the_structured_list() -> None:
    """The b-cp2 retirement, cited as a ``retires`` entry, is granted as the file grants it.

    One cited unit is a lead-in (a statement ending ``:`` before a list), which
    only a compact intake can cite.
    """
    issue, recorded_text = read("b", 2)
    recorded = featuredef.parse(recorded_text, True, True, True)
    found = compact.units(issue, (), V1)
    definition = reply(recorded, found)
    decisions = rows(definition, "decisions")
    mine = [d for d in decisions if text(d, "text").startswith("retires `")]
    assert len(mine) == 1
    said = _RETIRES.match(text(mine[0], "text"))
    assert said is not None
    test, reason = said.groups()
    definition["decisions"] = [d for d in decisions if d is not mine[0]]
    definition["retires"] = [{"test": test, "sources": mine[0]["sources"], "reason": reason}]
    checked = compact.check(definition, found, issue)
    assert checked.blocking == ()
    assert checked.retired == (test,)
    statements = normative.statements(issue, V1)
    lead_ins = {item.id for item, (_, lead) in zip(found.items, statements, strict=False) if lead}
    assert lead_ins & set(strings(mine[0], "sources")), "it cites a lead-in"
    rendered = featuredef.parse(checked.rendered, True, True, True)
    assert featuredef.retired_tests(rendered.decisions, issue) == [test]


@pytest.mark.parametrize(("cohort", "number"), CASES)
def test_the_compact_reply_is_smaller_than_the_recorded_file(cohort: str, number: int) -> None:
    """Print the bytes of the recorded file, the compact reply and the reply without rung-1
    decisions.

    For information (bytes, not tokens): the last column drops the decisions at
    rung 1, which the compact prompt does not ask for.
    """
    issue, recorded_text = read(cohort, number)
    recorded = featuredef.parse(recorded_text, True, True, True)
    definition = reply(recorded, compact.units(issue, (), V1))
    chosen = [d for d in rows(definition, "decisions") if text(d, "rung") != "1"]
    full = len(json.dumps({**BODY, "definition": definition}, ensure_ascii=False).encode())
    lean = len(
        json.dumps(
            {**BODY, "definition": {**definition, "decisions": chosen}},
            ensure_ascii=False,
        ).encode()
    )
    print(
        f"{cohort}-cp{number}: recorded file {len(recorded_text.encode())} B, "
        f"compact reply {full} B, without rung-1 decisions {lean} B"
    )
    assert lean < full < len(recorded_text.encode())
