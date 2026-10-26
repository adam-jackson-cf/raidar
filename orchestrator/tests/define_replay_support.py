"""A recorded feature definition read back as the compact reply that says the same.

factory-x-loops' ``evaluation.replay.definition`` helpers (``overlapping``, ``decision``,
``criterion``, ``reply``), kept as test support: the replay of recorded definitions is the
evaluation of the define capability, not part of the tool.
"""

from __future__ import annotations

import re

from raidar.subject.workflow.define_tool import compact, featuredef, normative
from raidar.subject.workflow.define_tool.core import JsonObject

_QUOTED = re.compile(r"[\"“]([^\"“”]+)[\"”]")
_RUNG = re.compile(r"rung\s*(\d)")
_REF = re.compile(r"`([^`\s:]+:\d+(?:-\d+)?)`")


def overlapping(quote: str, found: compact.Units) -> tuple[str, ...]:
    """Return the ``U`` then ``C`` ids ``quote`` shares a whole word-run with.

    Only units the intake is offered (``citable``): a fragment of a code block
    is no unit it could have cited.
    """
    ids: list[str] = []
    for prefix in ("U", "C"):
        group = [item for item in found.items if item.id.startswith(prefix) and item.citable]
        every = [normative.Unit(item.text, ()) for item in group]
        ids += [group[index].id for index in normative.sharing(quote, every)]
    return tuple(ids)


def decision(bullet: str, issue: compact.Units) -> JsonObject:
    """Return a recorded decision bullet as the reply's decision entry."""
    head, _, tail = bullet.rpartition(" (source:")
    if not head:
        head, tail = bullet, ""
    quotes = _QUOTED.findall(tail)
    ids = [i for quote in quotes for i in overlapping(quote, issue)]
    rung = _RUNG.search(tail)
    return {
        "text": head,
        "rung": rung.group(1) if rung else "1",
        "sources": list(dict.fromkeys(ids)),
        "refs": _REF.findall(tail),
        "reverse": "",
        "risk": featuredef.REQUIRES_REVIEW
        if featuredef.REQUIRES_REVIEW in bullet.casefold()
        else "",
    }


def criterion(item: featuredef.Criterion, issue: compact.Units) -> JsonObject:
    """Return a recorded acceptance criterion as the reply's criterion entry."""
    ids = [i for quote in item.sources for i in overlapping(quote, issue)]
    return {
        "id": item.id,
        "text": item.text,
        "sources": list(dict.fromkeys(ids)),
        "kind": "regression" if item.kind == "regression" else "behaviour",
        "aspects": [{"situation": a.situation, "outcome": a.outcome} for a in item.aspects],
        "examples": [{"input": e.given, "expected": e.expected} for e in item.examples],
    }


def reply(recorded: featuredef.FeatureDefinition, issue: compact.Units) -> JsonObject:
    """Return the compact ``definition`` that says what the recorded one says."""
    features = [
        {
            "id": feature.id,
            "name": feature.name,
            "outcome": feature.outcome,
            "risk": "",
            "criteria": [criterion(item, issue) for item in feature.traced],
        }
        for feature in recorded.features
    ]
    return {
        "title": recorded.title,
        "objective": recorded.sections[featuredef.OBJECTIVE].strip(),
        "features": features,
        "decisions": [decision(item, issue) for item in recorded.decisions],
        "retires": [],
    }
