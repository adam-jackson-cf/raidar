"""Sample intake replies and the ``define`` blocks that build them, for the define tests.

The greeting issue (``BODY``), its definition (``body``) and an intake reply object
(``ready``, ``asks``), with ``block_of`` turning a reply into the ``define put`` block that
builds it. Taken from factory-x-loops' ``tests/engine_support`` (``dbv_samples``,
``intake_samples``, ``defining``).
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.subject.workflow.define_tool import block
from raidar.subject.workflow.define_tool.block import Item
from raidar.subject.workflow.define_tool.core import JsonObject, rows, text

FEATURE = "F1 Greeting"


def question(feature: str = FEATURE, **over: object) -> JsonObject:
    value: JsonObject = {
        "feature": feature,
        "kind": "objective_unclear",
        "question": f"What is {feature} for?",
        "proposal": f"{feature} reports liveness only.",
        "options": [f"{feature} also reports readiness."],
    }
    return {**value, **over}


def named(
    name: str = FEATURE, behaviour_from: str = "issue", evidence: str | None = None
) -> JsonObject:
    """One entry of an intake reply's ``features``.

    By default ``none`` has empty evidence and ``issue`` cites the unit ``U2``.
    """
    if evidence is None:
        evidence = {"none": "", "issue": "U2"}.get(behaviour_from, "")
    return {"name": name, "behaviour_from": behaviour_from, "evidence": evidence}


BODY = (
    "Add a greeting endpoint.\n\n"
    "GET /greet returns 200 with the text `hello <name>`, where `name` is the "
    "`name` query parameter.\n"
    "Any other method on /greet is refused.\n"
)


# The feature the issue names, with the unit that says what it does.
FEATURES: tuple[JsonObject, ...] = (named("F1 Greeting", "issue", "U2"),)


AC = ("F1.AC1", "F1.AC2", "F1.AC3", "F1.AC4", "F1.AC5")


# Each criterion's aspects: (situation, outcome), in order.
ASPECTS: dict[str, tuple[tuple[str, str], ...]] = {
    AC[0]: (
        ("GET /greet with `name=Ada`", "the reply status is `200`"),
        ("GET /greet with `name=Ada`", "the reply text is `hello Ada`"),
    ),
    AC[1]: (("POST /greet", "the reply status is `405`"),),
    AC[2]: (("GET /greet with an empty `name`", "the reply text is `hello `"),),
    AC[3]: (("GET /greet with the one-letter `name=a`", "the reply status is `200`"),),
    AC[4]: (('GET /greet with `name=" Ada "`', "the reply text is `hello  Ada `"),),
}


EXAMPLES = {
    AC[0]: "GET /greet with `name=Ada` -> `200` and `hello Ada`",
    AC[2]: "GET /greet with `name=` -> `hello `",
    AC[3]: "GET /greet with `name=a` -> `200`",
    AC[4]: 'GET /greet with `name=" Ada "` -> `hello  Ada `',
}


# Where each criterion's behaviour is stated (the unit ids it cites).
SOURCES = {
    "F1.AC1": ["U2", "U1"],
    "F1.AC2": ["U3"],
    "F1.AC3": ["U2"],
    "F1.AC4": ["U2"],
    "F1.AC5": ["U2"],
}


TEXTS = {
    "F1.AC1": "GET /greet with `name=Ada` returns 200 with `hello Ada`.",
    "F1.AC2": "Every method on /greet but GET is refused with 405.",
    "F1.AC3": "An empty `name` greets with `hello ` and nothing after it.",
    "F1.AC4": "A one-letter `name` is accepted.",
    "F1.AC5": "Spaces around `name` are kept as given.",
}


def criterion(ident: str, **over: object) -> JsonObject:
    """One criterion of the greeting definition; ``over`` replaces its fields.

    The engine renders every field on one line and keeps the spaces inside it, so
    a literal with consecutive spaces (AC5's) survives as given.
    """
    example = EXAMPLES.get(ident)
    given, _, expected = example.partition(" -> ") if example else ("", "", "")
    value: JsonObject = {
        "id": ident,
        "text": TEXTS[ident],
        "sources": SOURCES[ident],
        "kind": "behaviour",
        "aspects": [
            {"situation": situation, "outcome": outcome} for situation, outcome in ASPECTS[ident]
        ],
        "examples": [{"input": given, "expected": expected}] if example else [],
    }
    return {**value, **over}


def regressed(ident: str) -> JsonObject:
    """The criterion ``ident`` as a ``Kind: regression`` one: no aspects, no examples."""
    return criterion(ident, kind="regression", aspects=[], examples=[])


def body(criteria: Sequence[JsonObject] | None = None, **over: object) -> JsonObject:
    """The greeting feature definition as an intake's reply carries it."""
    found = [criterion(ident) for ident in AC] if criteria is None else criteria
    value: JsonObject = {
        "title": "Greeting endpoint",
        "objective": "Greet a caller by name.",
        "features": [
            {
                "id": "F1",
                "name": "Greeting",
                "outcome": "GET /greet answers with a greeting for the caller's name",
                "risk": "",
                "criteria": list(found),
            }
        ],
        "decisions": [
            {
                "text": "405 is the refusal",
                "rung": "3",
                "sources": [],
                "refs": [],
                "reverse": "by returning 404",
                "risk": "",
            }
        ],
        "retires": [],
    }
    return {**value, **over}


EMPTY: JsonObject = {
    "title": "",
    "objective": "",
    "features": [],
    "decisions": [],
    "retires": [],
}


def plain_ready(
    replies: Sequence[JsonObject] = (), features: Sequence[JsonObject] = FEATURES
) -> JsonObject:
    """An intake reply that nothing blocks a feature."""
    return {
        "features": list(features),
        "status": "ready",
        "questions": [],
        "replies": list(replies),
    }


def plain_asks(
    *questions: JsonObject,
    replies: Sequence[JsonObject] = (),
    features: Sequence[JsonObject] = FEATURES,
) -> JsonObject:
    return {
        "features": list(features),
        "status": "needs_input",
        "questions": list(questions) or [question()],
        "replies": list(replies),
    }


def ready(
    definition: JsonObject | None = None,
    features: Sequence[JsonObject] = FEATURES,
    replies: Sequence[JsonObject] = (),
) -> JsonObject:
    """An intake reply that nothing blocks a feature."""
    return {
        **plain_ready(replies=replies, features=features),
        "definition": body() if definition is None else definition,
    }


def asks(
    *questions: JsonObject,
    replies: Sequence[JsonObject] = (),
    features: Sequence[JsonObject] = FEATURES,
) -> JsonObject:
    """An intake reply that asks: it sends its definition empty."""
    return {
        **plain_asks(*questions, replies=replies, features=features),
        "definition": EMPTY,
    }


def reply(question_id: str, outcome: str = "accepted", quote: str = "/accept") -> JsonObject:
    return {"question": question_id, "outcome": outcome, "quote": quote}


def items(reply: JsonObject) -> list[Item]:
    """Return the items of an intake reply object, as a ``put`` block would hold them."""
    definition = reply.get("definition")
    held = definition if isinstance(definition, dict) else {}
    found: list[Item] = []
    for kind, key in ((block.TITLE, "title"), (block.OBJECTIVE, "objective")):
        if text(held, key):
            found.append(Item(kind, kind, {"text": text(held, key)}))
    found += [
        Item(block.NAMED, " ".join(text(named, "name").casefold().split()), named)
        for named in rows(reply, "features")
    ]
    for feature in rows(held, "features"):
        data = {key: value for key, value in feature.items() if key != "criteria"}
        ident = text(feature, "id")
        found.append(Item(block.FEATURE, ident, data))
        found += [
            Item(block.AC, text(criterion, "id"), criterion, ident)
            for criterion in rows(feature, "criteria")
        ]
    found += [
        Item(block.DECISION, str(number), decision)
        for number, decision in enumerate(rows(held, "decisions"), start=1)
    ]
    found += [Item(block.RETIRE, text(r, "test"), r) for r in rows(held, "retires")]
    found += [
        Item(block.QUESTION, block.question_key(text(q, "feature"), text(q, "kind")), q)
        for q in rows(reply, "questions")
    ]
    found += [Item(block.REPLY, text(r, "question"), r) for r in rows(reply, "replies")]
    return found


def block_of(reply: JsonObject) -> str:
    """Return the ``define put`` block that builds ``reply``."""
    return block.show(items(reply))
