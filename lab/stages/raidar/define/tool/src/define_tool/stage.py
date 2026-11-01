"""``python -m define_tool.stage``: the define package's setup, stop and gate calls.

Raidar calls these with a root-owned JSON context (``--context <path>`` for setup and stop,
``RAIDAR_STAGE_CONTEXT`` for the gate) holding ``task``, ``params``, ``carry``, ``state`` and
``outputs``. Every call rebuilds the tool's own context from it, never from the agent-writable
state, so the units the checks read cannot be changed from inside the workspace.

``setup`` empties the state, writes the tool's context into it and prints the ``UNITS`` token.
``inspect`` (the stop) reads the draft: ready writes the definition and the ``criteria/1``
document and is ``done``; sealed ``ask`` accepts every proposal and ``resume``s (at most
``max_refinement_rounds`` times, then ``closed``); anything else is repaired once (``resume``),
then ``closed``. ``sealed`` (the gate's prerequisite) passes only for a draft that is ready,
and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import compact, featuredef, status, tool
from .aspects import REGRESSION, Aspect, Example
from .core import JsonObject, strings
from .draft import DraftError
from .unitrules import VERSIONS

CONTEXT_ENV = "RAIDAR_STAGE_CONTEXT"
WORKDIR = "/app"
CRITERIA_CONTRACT = "criteria/1"
CRITERIA_OUTPUT = "criteria"
DEFINITION_OUTPUT = "definition"
REPAIR = (
    "The definition could not be used: {error}. Run `define check`, fix what it "
    "lists, then run `define done ready` (or `define done ask` when you are "
    "asking questions). Then finish the build against `/app/.raidar/feature-definition.md`."
)
ACCEPTED = (
    "Every proposal is accepted (benchmark policy). Build a fresh definition with these "
    "answers; seal it with `define done ready`, then finish building against the "
    "definition.\n\n"
)


def _result(
    state: str, message: str = "", carry: Mapping[str, object] | None = None
) -> dict[str, object]:
    return {"status": state, "message": message, "tokens": {}, "carry": dict(carry or {})}


def _tool_context(raidar: Mapping[str, object], said: Sequence[str]) -> tool.Context:
    params = raidar.get("params")
    rules = params.get("unit_rules") if isinstance(params, dict) else None
    if isinstance(rules, bool) or not isinstance(rules, int) or rules not in VERSIONS:
        raise ValueError(f"unit_rules {rules!r} names no unit rule set")
    task = raidar.get("task")
    return tool.Context(str(task), tuple(said), "", (), WORKDIR, rules)


def _carry(raidar: Mapping[str, object]) -> JsonObject:
    carried = raidar.get("carry")
    return dict(carried) if isinstance(carried, dict) else {}


def _path(raidar: Mapping[str, object], key: str, name: str | None = None) -> Path:
    found = raidar.get(key)
    if name is not None:
        found = found.get(name) if isinstance(found, dict) else None
    if not isinstance(found, str):
        raise ValueError(f"the context names no {name or key}")
    return Path(found)


def _units(context: tool.Context) -> str:
    return compact.units(context.issue, list(context.said), VERSIONS[context.unit_rules]).text()


def setup(raidar: Mapping[str, object]) -> dict[str, object]:
    """Empty the state for this checkpoint; return the numbered units of the task."""
    context = _tool_context(raidar, [])
    tool.start(_path(raidar, "state"), context)
    _path(raidar, "outputs", DEFINITION_OUTPUT).unlink(missing_ok=True)
    return {"tokens": {"UNITS": _units(context)}}


def _aspects(items: Sequence[Aspect]) -> list[dict[str, str]]:
    return [{"situation": item.situation, "outcome": item.outcome} for item in items]


def _examples(items: Sequence[Example]) -> list[dict[str, str]]:
    """An example's input is the situation and its expected value the outcome."""
    return [{"situation": item.given, "outcome": item.expected} for item in items]


def criteria_document(rendered: str) -> JsonObject:
    """The ``criteria/1`` document of a rendered feature definition."""
    parsed = featuredef.parse(rendered, traced=True, anchored=True, aspects=True)
    features: list[JsonObject] = []
    for feature in parsed.features:
        criteria: list[JsonObject] = [
            {
                "id": item.id,
                "criterion": item.text,
                "verification": item.verification,
                "sources": list(item.sources),
                "kind": "regression" if item.kind == REGRESSION else "new",
                "aspects": _aspects(item.aspects),
                "examples": _examples(item.examples),
            }
            for item in feature.traced
        ]
        features.append(
            {
                "id": feature.id,
                "name": feature.name,
                "outcome": feature.outcome,
                "criteria": criteria,
            }
        )
    return {"contract": CRITERIA_CONTRACT, "features": features}


def _rendered(document: JsonObject) -> str:
    checked = document["checked"]
    file = checked.get("file") if isinstance(checked, dict) else None
    rendered = file.get("rendered") if isinstance(file, dict) else None
    return rendered if isinstance(rendered, str) else ""


def _findings(document: JsonObject) -> list[str]:
    found = [*strings(document, "blocking"), *strings(document, "schema_violations")]
    if found:
        return found
    return [f"definition is {document['state']}: run `define check`"]


def _ready(raidar: Mapping[str, object], document: JsonObject, carry: JsonObject) -> JsonObject:
    rendered = _rendered(document)
    tool.write_definition(_path(raidar, "outputs", DEFINITION_OUTPUT), rendered)
    criteria = json.dumps(criteria_document(rendered), sort_keys=True) + "\n"
    tool.write_definition(_path(raidar, "outputs", CRITERIA_OUTPUT), criteria)
    return _result("done", carry=carry)


def _accept(
    raidar: Mapping[str, object], document: JsonObject, carry: JsonObject, rounds: int
) -> JsonObject:
    asked = document["reply"]
    questions = asked.get("questions") if isinstance(asked, dict) else None
    items = [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []
    number = int(str(carry.get("asked", 0))) + 1
    if number > rounds:
        return _result("closed", "more questions than the benchmark accepts", carry)
    said = list(strings(carry, "said")) + [
        f"- Q{number}.{i} ({q.get('feature', '')}) {q.get('question', '')} → accepted: "
        f"{q.get('proposal', '')}"
        for i, q in enumerate(items, 1)
    ]
    context = _tool_context(raidar, said)
    tool.next_round(_path(raidar, "state"), context)
    carried = {**carry, "said": said, "asked": number}
    return _result("resume", ACCEPTED + _units(context), carried)


def _assess(raidar: Mapping[str, object], context: tool.Context) -> JsonObject:
    try:
        return status.assess(_path(raidar, "state"), context)
    except DraftError as error:
        return {"state": "unusable", "blocking": [str(error)]}


def inspect(raidar: Mapping[str, object], params: Mapping[str, object]) -> JsonObject:
    """The stop result for the draft the builder left."""
    carry = _carry(raidar)
    context = _tool_context(raidar, strings(carry, "said"))
    document = _assess(raidar, context)
    if document["state"] == status.READY:
        return _ready(raidar, document, carry)
    if document["state"] == "asking":
        return _accept(raidar, document, carry, int(str(params.get("max_refinement_rounds", 0))))
    if carry.get("repaired"):
        return _result("closed", "the definition stayed unusable after one repair", carry)
    message = REPAIR.format(error="; ".join(_findings(document)))
    return _result("resume", message, {**carry, "repaired": True})


def sealed(raidar: Mapping[str, object]) -> int:
    """The gate prerequisite: 0 only for a ready draft; the findings otherwise. Writes nothing."""
    carry = _carry(raidar)
    document = _assess(raidar, _tool_context(raidar, strings(carry, "said")))
    if document["state"] == status.READY:
        return 0
    sys.stdout.write("definition_not_ready: " + "; ".join(_findings(document)) + "\n")
    return tool.FAULT


def _read(argv: Sequence[str]) -> tuple[argparse.Namespace, dict[str, object]]:
    parser = argparse.ArgumentParser(prog="define-stage")
    parser.add_argument("call", choices=("setup", "inspect", "sealed"))
    parser.add_argument("--context")
    args = parser.parse_args(argv)
    path = args.context or os.environ.get(CONTEXT_ENV)
    if not path:
        parser.error(f"no context: pass --context or set {CONTEXT_ENV}")
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        parser.error("the context is not a JSON object")
    return args, document


def main(argv: Sequence[str] | None = None) -> int:
    """Run one call; setup and inspect print their JSON result."""
    args, raidar = _read(sys.argv[1:] if argv is None else argv)
    try:
        if args.call == "sealed":
            return sealed(raidar)
        params = raidar.get("params")
        if args.call == "setup":
            result = setup(raidar)
        else:
            result = inspect(raidar, params if isinstance(params, dict) else {})
    except (OSError, ValueError, DraftError) as error:
        sys.stdout.write(json.dumps({"error": str(error)}) + "\n")
        return tool.BROKEN
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
