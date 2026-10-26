"""The shared structured reply: one parser, a skeleton and the repair prompts.

Ported from factory-x-loops ``core/structured.py`` at ``ac5db0f`` (the reply parser: the last
top-level JSON object, undeclared properties dropped and listed, every violation listed) and
``engine/workflow/schemas.py`` / ``stages.py`` (the build reply schema, its skeleton sentence and
its repair prompt). Pure functions: no I/O.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator

JsonObject = dict[str, Any]

MAX_VIOLATIONS = 8
# The longest a single violation's message runs: the validator quotes the value it refused.
VIOLATION_WIDTH = 200


# A `{` that can open a JSON object: the next character that is not white space
# is a `"` (a key) or a `}` (empty).
OBJECT_START = re.compile(r'\{\s*["}]')
BRACES = re.compile(r"[{}]")
# What follows a `}` when the object it seems to close runs on: JSON punctuation.
RUNS_ON = re.compile(r"\s*[,\]}:]")
# The characters of the reply shown on each side of a parse fault.
EXCERPT = 30


class _FaultError(ValueError):
    """An object that does not parse; ``truncated`` when the reply ended inside it."""

    def __init__(self, message: str, truncated: bool) -> None:
        super().__init__(message)
        self.truncated = truncated


def _decode(text: str, begin: int, length: int) -> tuple[JsonObject, int]:
    """Return the JSON object that starts at ``begin`` and the index just after it.

    ``length`` is the reply's length less any trailing white space.

    Raises
    ------
        _FaultError: the object does not parse; the message names the parser's
            error, its character position and a short excerpt around it.
    """
    try:
        value, end = json.JSONDecoder().raw_decode(text, begin)
    except json.JSONDecodeError as failure:
        near = text[max(0, failure.pos - EXCERPT) : failure.pos + EXCERPT]
        ran_out = failure.pos >= length or failure.msg.startswith("Unterminated string")
        raise _FaultError(f"reply is not valid JSON: {failure} near {near!r}", ran_out) from None
    except RecursionError:
        raise _FaultError("reply is not valid JSON: objects nested too deeply", True) from None
    return value, end


def _closings(text: str) -> dict[int, int]:
    """Return, for each `{` of ``text`` that a later `}` closes, the index of that `}`.

    Braces are paired by counting alone, in one pass, strings ignored: it only
    bounds a broken object, which no parser can.
    """
    closes: dict[int, int] = {}
    opened: list[int] = []
    for brace in BRACES.finditer(text):
        if brace.group() == "{":
            opened.append(brace.start())
        elif opened:
            closes[opened.pop()] = brace.start()
    return closes


def _runs_on(text: str, close: int | None, failure: _FaultError) -> bool:
    """Return whether a broken object goes on past the `}` paired with its `{`."""
    if close is None:
        return failure.truncated
    return RUNS_ON.match(text, close + 1) is not None


def _carried(
    text: str,
    close: int | None,
    keyed: bool,
    failure: _FaultError,
    broken: _FaultError | None,
) -> _FaultError | None:
    """Return the keyed fault the reading carries after ``failure``; raise one that runs on.

    ``close`` is the `}` paired with the failed object's `{`, if any.
    """
    if not keyed:
        return broken
    if _runs_on(text, close, failure):
        raise failure
    return failure if close is not None else broken


def extract_object(text: str) -> JsonObject:
    """Return the last top-level JSON object in a harness's reply text.

    Prose or a code fence before or after it is fine. Objects are read left to
    right, each outermost one whole, from every `{` that is followed by a key
    or a `}` or that a `}` pairs with; the last that parses wins. An object
    that opens with a key and does not parse is the answer, broken, when the
    reply ended inside it or when JSON punctuation (`,`, `]`, `}`, `:`)
    follows the `}` its `{` pairs with: brackets out of balance, so the object
    runs on and what follows is its own inner objects. Its fault is raised and
    no other object is taken in its place. Any other `{` that a `}` pairs with
    but that does not parse is passed over whole: an example before the answer
    is fine, but a keyed one after the last object that parsed is the answer,
    broken, and its fault is raised, never the earlier object returned in its
    place; one that opens no key (`then { see above }`) raises only when no
    object parses. Any other `{` (`use { then ...`, an
    illustrative `{"verdict": ...` that nothing closes) opens nothing and
    reading goes on after it.

    Raises
    ------
        ValueError: the text holds no JSON object, or the answer does not parse
            (see ``_decode`` for the message).
    """
    closes = _closings(text)
    length = len(text.rstrip())
    found: JsonObject | None = None
    fault: _FaultError | None = None
    # A keyed object's fault, read after the last object that parsed.
    broken: _FaultError | None = None
    cursor = 0
    while (start := text.find("{", cursor)) != -1:
        cursor = start + 1
        keyed = OBJECT_START.match(text, start) is not None
        if start not in closes and not keyed:
            continue
        try:
            found, cursor = _decode(text, start, length)
            broken = None
        except _FaultError as failure:
            broken = _carried(text, closes.get(start), keyed, failure, broken)
            if start in closes:
                fault, cursor = failure, closes[start] + 1
    return _answer(found, fault, broken)


def _answer(
    found: JsonObject | None, fault: _FaultError | None, broken: _FaultError | None
) -> JsonObject:
    """Return the reading's answer: ``broken`` raised, else ``found``, else ``fault`` raised."""
    if broken is not None:
        raise broken
    if found is not None:
        return found
    if fault is not None:
        raise fault
    raise ValueError("reply holds no JSON object")


def _pointer(parts: Iterable[object]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def drop_unknown(value: object, schema: JsonObject) -> tuple[object, list[str]]:
    """Remove every property the schema does not declare, at any depth.

    Returns the cleaned value and a JSON pointer for each property dropped.
    Nothing is coerced or moved: a property in the wrong place is dropped like
    any other, and its absence where it belongs is the schema's to report.
    """
    dropped: list[str] = []

    def clean(found: object, rule: object, path: tuple[object, ...]) -> object:
        if not isinstance(rule, dict):
            return found
        if isinstance(found, list):
            return [
                clean(item, rule.get("items"), (*path, index)) for index, item in enumerate(found)
            ]
        properties = rule.get("properties")
        if not isinstance(found, dict) or not isinstance(properties, dict):
            return found
        kept: dict[str, object] = {}
        for name, item in found.items():
            if name in properties:
                kept[name] = clean(item, properties[name], (*path, name))
            else:
                dropped.append(_pointer((*path, name)))
        return kept

    return clean(value, schema, ()), dropped


def bounded(items: list[str], limit: int = MAX_VIOLATIONS) -> list[str]:
    """Return the first ``limit`` of ``items``, then ``(and N more)`` when any were cut."""
    if len(items) <= limit:
        return items
    return [*items[:limit], f"(and {len(items) - limit} more)"]


def violations(value: object, schema: JsonObject, limit: int = MAX_VIOLATIONS) -> list[str]:
    """Return the ways ``value`` breaks ``schema``, path-qualified, the first ``limit``.

    Each is ``<JSON pointer>: <message>`` (``(root)`` for the value itself), in
    path order. When more than ``limit`` are found the list ends with
    ``(and N more)``, so a reader knows it is a prefix.
    """
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    found = [
        f"{_pointer(error.absolute_path) or '(root)'}: {error.message[:VIOLATION_WIDTH]}"
        for error in errors
    ]
    return bounded(found, limit)


def skeleton(schema: JsonObject) -> str:
    """Render ``schema`` as a compact JSON-shaped line: each leaf its type or enum.

    A leaf is its JSON type name (``string``) or, for an enum, its values
    (``"a" | "b"``); a list of types is joined (``string | null``); an array
    holds one example item.

    Raises
    ------
        ValueError: a node has neither a ``type`` nor an ``enum``, which this
            rendering cannot state (``anyOf``, ``$ref``).
    """
    options = schema.get("enum")
    if isinstance(options, list):
        return " | ".join(json.dumps(option) for option in options)
    shaped = _composite(schema)
    if shaped is not None:
        return shaped
    kind = schema.get("type")
    if isinstance(kind, str):
        return kind
    if isinstance(kind, list) and kind:
        return " | ".join(str(name) for name in kind)
    raise ValueError(f"schema node has no type or enum: {json.dumps(schema)}")


def _composite(schema: JsonObject) -> str | None:
    """The rendering of an object with properties or an array with items, else None."""
    kind = schema.get("type")
    properties = schema.get("properties")
    if kind == "object" and isinstance(properties, dict):
        fields = (f"{json.dumps(name)}: {skeleton(rule)}" for name, rule in properties.items())
        return "{" + ", ".join(fields) + "}"
    items = schema.get("items")
    if kind == "array" and isinstance(items, dict):
        return f"[{skeleton(items)}]"
    return None


REPAIR_PROMPT = (
    "Your last reply could not be used: {error}. Reply again with ONLY a JSON "
    "object of the shape given, and nothing else."
)
"""factory-x-loops ``engine/workflow/stages.py`` ``REPAIR``, for a syntax and a semantic fault."""
SHAPE = (
    "Reply with ONLY one JSON object of this shape, and nothing else (no prose, "
    "no code fence). Every property shown is required and holds a value of the "
    'type shown (`""` or `[]` when there is nothing to say); `"a" | "b"` lists '
    "the only values allowed; an array holds any number of the item shown. Add "
    "no other property.\n\n{shape}"
)
"""The one statement of a reply's shape (``stages.py`` ``SHAPE``), ``{shape}`` its skeleton."""


def _string() -> JsonObject:
    return {"type": "string"}


def _object(**properties: JsonObject) -> JsonObject:
    return {"type": "object", "required": list(properties), "properties": properties}


def _rows(**properties: JsonObject) -> JsonObject:
    return {"type": "array", "items": _object(**properties)}


BUILD_SCHEMA: JsonObject = _object(
    account=_string(),
    adrs=_rows(
        id=_string(),
        status=_string(),
        context=_string(),
        decision=_string(),
        consequences=_string(),
    ),
    ac_tests=_rows(criterion=_string(), tests={"type": "array", "items": _string()}),
)
"""An audited builder turn's reply (factory-x-loops ``schemas.BUILD_AUDIT_SCHEMA``)."""


def shape_suffix(schema: JsonObject) -> str:
    """The sentence appended to a prompt that asks for a reply of ``schema``'s shape."""
    return "\n\n" + SHAPE.format(shape=skeleton(schema))


def read_reply(text: str, schema: JsonObject) -> tuple[JsonObject | None, str | None, list[str]]:
    """Read a reply: ``(value, error, dropped)``.

    The last JSON object of the text is taken, properties the schema does not declare are
    dropped (listed as JSON pointers, never refused) and every remaining violation is listed so
    one repair can fix them all; a refused reply names what was dropped too, so a property at
    the wrong level is not mistaken for one that was read. ``value`` is None when ``error`` is set.
    """
    try:
        found = extract_object(text)
    except ValueError as error:
        return None, str(error), []
    value, dropped = drop_unknown(found, schema)
    assert isinstance(value, dict)
    faults = violations(value, schema)
    if not faults:
        return value, None, dropped
    refused = f"reply violates the schema: {'; '.join(faults)}"
    if dropped:
        refused += f"; ignored: {', '.join(bounded(dropped))}"
    return None, refused, dropped
