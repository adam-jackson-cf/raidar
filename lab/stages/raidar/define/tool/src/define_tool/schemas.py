"""The shape of the reply a sealed draft assembles (``Draft.reply``), and a check of it.

``INTAKE_REPLY_SCHEMA`` is factory-x-loops' ``engine.workflow.schemas`` one, unchanged. The engine
validated a sealed reply against it with a JSON Schema library; ``violations`` does the same for
the few keywords this schema uses (``type``, ``enum``, ``required``, ``properties``, ``items``),
so the payload needs no package beyond the standard library.
"""

from __future__ import annotations

from .core import JsonObject
from .intakecheck import FEATURE_SOURCES, QUESTION_KINDS, REPLY_OUTCOMES

MAX_VIOLATIONS = 8


def _s() -> JsonObject:
    return {"type": "string"}


def _ls() -> JsonObject:
    return {"type": "array", "items": {"type": "string"}}


def _e(*values: str) -> JsonObject:
    return {"type": "string", "enum": list(values)}


def _obj(**properties: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "required": list(properties),
        "properties": properties,
    }


def _list(**properties: JsonObject) -> JsonObject:
    return {"type": "array", "items": _obj(**properties)}


_QUESTIONS = _list(
    feature=_s(),
    kind=_e(*QUESTION_KINDS),
    question=_s(),
    proposal=_s(),
    options=_ls(),
)
_REPLIES = _list(question=_s(), outcome=_e(*REPLY_OUTCOMES), quote=_s())
_FEATURES = _list(name=_s(), behaviour_from=_e(*FEATURE_SOURCES), evidence=_s())

# A `needs_input` reply sends `definition` empty (`""` and `[]`) and the checks ignore it. A rung
# is the string `"1"` to `"5"` and a `risk` is `""` or `"requires review"`; the schema sets no
# other limit (the id and source rules, and the question limit, are the checks', not `maxItems`).
INTAKE_REPLY_SCHEMA: JsonObject = _obj(
    features=_FEATURES,
    status=_e("ready", "needs_input"),
    questions=_QUESTIONS,
    replies=_REPLIES,
    definition=_obj(
        title=_s(),
        objective=_s(),
        features=_list(
            id=_s(),
            name=_s(),
            outcome=_s(),
            risk=_e("", "requires review"),
            criteria=_list(
                id=_s(),
                text=_s(),
                sources=_ls(),
                kind=_e("behaviour", "regression"),
                aspects=_list(situation=_s(), outcome=_s()),
                examples=_list(input=_s(), expected=_s()),
            ),
        ),
        decisions=_list(
            text=_s(),
            rung=_e("1", "2", "3", "4", "5"),
            sources=_ls(),
            refs=_ls(),
            reverse=_s(),
            risk=_e("", "requires review"),
        ),
        retires=_list(test=_s(), sources=_ls(), reason=_s()),
    ),
)

_TYPES: dict[str, type] = {"object": dict, "array": list, "string": str}


def _pointer(parts: tuple[object, ...]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _walk(value: object, schema: JsonObject, where: tuple[object, ...]) -> list[str]:
    here = _pointer(where) or "/"
    kind = str(schema["type"])
    if not isinstance(value, _TYPES[kind]):
        return [f"{here}: {value!r:.100} is not of type {kind!r}"]
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        return [f"{here}: {value!r:.100} is not one of {allowed!r}"]
    if isinstance(value, dict):
        return _members(value, schema, where)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        items = schema["items"]
        assert isinstance(items, dict)
        return [
            found
            for index, item in enumerate(value)
            for found in _walk(item, items, (*where, index))
        ]
    return []


def _members(value: dict[str, object], schema: JsonObject, where: tuple[object, ...]) -> list[str]:
    here = _pointer(where) or "/"
    required = schema.get("required")
    found = [
        f"{here}: {key!r} is a required property"
        for key in (required if isinstance(required, list) else [])
        if key not in value
    ]
    properties = schema.get("properties")
    for key, sub in (properties if isinstance(properties, dict) else {}).items():
        if key in value:
            found += _walk(value[key], sub, (*where, key))
    return found


def violations(value: object, schema: JsonObject, limit: int = MAX_VIOLATIONS) -> list[str]:
    """Return the ways ``value`` breaks ``schema``, path-qualified, the first ``limit``."""
    found = _walk(value, schema, ())
    if len(found) <= limit:
        return found
    return [*found[:limit], f"(and {len(found) - limit} more)"]
