"""Entry-point shim for spec-conformant reference variants (stdlib only).

``raidar.design.problems.variants`` copies this module's source into a reference
snapshot as the problem's entry file, with the reference's own entry file
renamed beside it. The shim runs the reference unchanged (same interpreter,
arguments, stdin, stderr and exit code) and rewrites only its stdout, when the
whole of stdout is one JSON document:

- ``neutral_error_text``: every error's human-readable text becomes
  ``NEUTRAL_TEXT``: the ``detail`` of an object that has an ``error`` key, and
  the ``message``/``detail`` of an ``error`` object. Categories, codes, other
  fields and the serialisation format are kept byte for byte where possible;
- ``reformatted_json``: the same JSON value re-serialised with two-space
  indentation and a trailing newline (key order kept).

Anything else on stdout passes through unchanged.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
from typing import Any

NEUTRAL_TEXT = "request rejected"
NEUTRAL_ERROR_TEXT = "neutral_error_text"
REFORMATTED_JSON = "reformatted_json"
ERROR_TEXT_FIELDS = ("detail", "message")


def neutralise(value: Any) -> Any:
    """``value`` with every error's human-readable text replaced by ``NEUTRAL_TEXT``."""
    if isinstance(value, list):
        return [neutralise(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: neutralise(item) for key, item in value.items()}
    if "error" in result and "detail" in result:
        result["detail"] = NEUTRAL_TEXT
    error = result.get("error")
    if isinstance(error, dict):
        for field in ERROR_TEXT_FIELDS:
            if field in error:
                error[field] = NEUTRAL_TEXT
    return result


def _formats() -> list[dict[str, Any]]:
    options = itertools.product((True, False), ((",", ":"), None), (None, 2, 4), (True, False))
    return [
        {"sort_keys": keys, "separators": separators, "indent": indent, "ensure_ascii": ascii_}
        for keys, separators, indent, ascii_ in options
    ]


def dump_like(original: str, before: Any, after: Any) -> str | None:
    """``after`` serialised the way ``before`` was serialised as ``original`` (None if unknown)."""
    body = original[:-1] if original.endswith("\n") else original
    newline = "\n" if original.endswith("\n") else ""
    for options in _formats():
        if json.dumps(before, **options) == body:
            return json.dumps(after, **options) + newline
    return None


def transform(stdout: str, variant: str) -> str:
    """The variant's stdout for the reference's ``stdout``."""
    try:
        value = json.loads(stdout)
    except ValueError:
        return stdout
    if variant == NEUTRAL_ERROR_TEXT:
        changed = neutralise(value)
        if changed == value:
            return stdout
        return dump_like(stdout, value, changed) or json.dumps(changed)
    if variant == REFORMATTED_JSON:
        reformatted = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        return reformatted if reformatted != stdout else json.dumps(value, indent=4) + "\n"
    raise ValueError(f"unknown variant {variant!r}")


def main(target: str, variant: str, argv: list[str]) -> int:
    """Run ``target`` with ``argv`` and write its stdout through ``transform``."""
    proc = subprocess.run([sys.executable, target, *argv], stdout=subprocess.PIPE, check=False)
    text = transform(proc.stdout.decode("utf-8", "surrogateescape"), variant)
    sys.stdout.buffer.write(text.encode("utf-8", "surrogateescape"))
    sys.stdout.flush()
    return proc.returncode
