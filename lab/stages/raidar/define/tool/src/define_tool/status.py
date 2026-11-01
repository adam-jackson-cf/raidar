"""``python -m define_tool.status``: what the define state holds now, as one JSON object.

The stage reads this when the builder stops, in place of re-deriving the checks: the draft is read
from the state directory (``RAIDAR_DEFINE_STATE``, ``/app/.raidar/define`` by default), schema
checked, checked again as a reply (``tool.check``) and rendered. ``--write`` also writes the
rendered definition (to ``--output``, else ``RAIDAR_DEFINE_OUTPUT``, else under the context's
workdir) when the state is ``ready``.

``state`` is ``unbuilt`` (no draft), ``unsealed``, ``asking`` (sealed ``ask``: ``reply`` holds the
questions), ``unusable`` (sealed, but the schema or a blocking finding refuses it) or ``ready``.
``tool_failed`` is true when the tool recorded an ``ERROR_CALL``. The exit code is 0 for ``ready``
and 1 for any other state; 126 when the state cannot be read at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import tool
from .core import JsonObject
from .draft import SEALS, Draft, DraftError
from .schemas import INTAKE_REPLY_SCHEMA, violations

READY = "ready"


def _findings(checked: tool.ReplyChecked) -> list[JsonObject]:
    return [
        {"text": item.text, "kind": item.kind, "advisory": item.advisory}
        for item in checked.findings
    ]


def _file(checked: tool.ReplyChecked) -> JsonObject | None:
    file = checked.file
    if file is None:
        return None
    return {
        "rendered": file.rendered,
        "sha256": hashlib.sha256(file.rendered.encode("utf-8")).hexdigest(),
        "uncovered": [unit.id for unit in file.uncovered],
        "notes": [note.text for note in file.notes],
        "retired": list(file.retired),
    }


def _state(held: bool, sealed: str, usable: bool) -> str:
    if not held:
        return "unbuilt"
    if not sealed:
        return "unsealed"
    if not usable:
        return "unusable"
    return "asking" if sealed == "ask" else READY


def assess(directory: Path, context: tool.Context | None = None) -> JsonObject:
    """Return what the state in ``directory`` holds; raise ``DraftError``, ``OSError`` or
    ``ValueError``.

    The draft is checked under ``context``: the one given, else the state's own
    (``context.json``). A draft not sealed (or none yet) is checked as the tool's own
    ``define check`` checks it, so its findings are the ones the builder is to repair.
    """
    if context is None:
        context = _context(directory / tool.CONTEXT_FILE)
    held = tool.read_draft(directory)
    draft = held if held is not None else Draft()
    failed = any(call.command == tool.ERROR_CALL for call in tool.read_calls(directory))
    status = SEALS[draft.sealed] if draft.sealed else draft.presumed()
    reply = draft.reply(status)
    faults = violations(reply, INTAKE_REPLY_SCHEMA)
    checked = tool.check(draft, context, status)
    usable = not faults and not checked.blocking
    state = _state(held is not None, draft.sealed, usable)
    return {
        "state": state,
        "tool_failed": failed,
        "draft": {"present": held is not None, "sealed": draft.sealed},
        "schema_violations": faults,
        "findings": _findings(checked),
        "blocking": list(checked.blocking),
        "checked": {"file": _file(checked)},
        "reply": reply,
    }


def _rendered(document: JsonObject) -> str | None:
    checked = document["checked"]
    file = checked.get("file") if isinstance(checked, dict) else None
    rendered = file.get("rendered") if isinstance(file, dict) else None
    return rendered if isinstance(rendered, str) else None


def _context(path: Path) -> tool.Context:
    return tool.Context.read(json.loads(path.read_text("utf-8")))


def _write_ready(
    args: argparse.Namespace, env: Mapping[str, str], context: tool.Context, document: JsonObject
) -> None:
    rendered = _rendered(document)
    if args.write and document["state"] == READY and rendered is not None:
        override = args.output or env.get(tool.OUTPUT_ENV)
        tool.write_definition(
            tool.definition_path(context, Path(override) if override else None), rendered
        )


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Print the assessment of the state; return its exit code (see the module docstring)."""
    parser = argparse.ArgumentParser(prog="define_tool.status")
    parser.add_argument("--write", action="store_true", help="write the ready definition")
    parser.add_argument("--output", help="where --write puts it")
    parser.add_argument("--context", help="the context to check under, not the state's own")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    directory = Path(env.get(tool.STATE_ENV) or tool.DEFAULT_STATE)
    try:
        context = _context(Path(args.context) if args.context else directory / tool.CONTEXT_FILE)
        document = assess(directory, context)
        _write_ready(args, env, context, document)
    except (OSError, ValueError, DraftError) as error:
        sys.stdout.write(json.dumps({"state": "error", "error": str(error)}) + "\n")
        return tool.BROKEN
    sys.stdout.write(json.dumps(document, sort_keys=True) + "\n")
    return 0 if document["state"] == READY else tool.FAULT


if __name__ == "__main__":
    raise SystemExit(main())
