"""Spec-conformant variants of a reference solution (the audit's dynamic half).

A variant is the reference snapshot with its entry file wrapped by
``raidar.design.problem.variant_shim``: the reference runs unchanged and only its
stdout is rewritten (``VARIANTS``). Each variant stays conforming wherever the
specification does not determine the rewritten part, so a case that passes on
the reference and fails on a variant asserts exactly that part. Whether the
specification determines it is the static half's question
(``raidar.design.problem.determinacy``).
"""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

from raidar.design.problem import variant_shim

VARIANTS: dict[str, str] = {
    variant_shim.NEUTRAL_ERROR_TEXT: (
        "every error's human-readable text (an error object's detail or message) replaced "
        f"by {variant_shim.NEUTRAL_TEXT!r}; category, exit code and format kept"
    ),
    variant_shim.REFORMATTED_JSON: (
        "JSON stdout re-serialised with two-space indentation and a trailing newline; "
        "value and key order kept"
    ),
}
ORIGINAL_PREFIX = "_raidar_audit_original_"


def original_name(entry_file: str) -> str:
    return f"{ORIGINAL_PREFIX}{entry_file}"


def shim_source(entry_file: str, variant: str) -> str:
    """The shim module's source plus the call that wraps the renamed entry file."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; known: {', '.join(VARIANTS)}")
    target = original_name(entry_file)
    return (
        inspect.getsource(variant_shim)
        + '\n\nif __name__ == "__main__":\n'
        + "    import os\n\n"
        + f"    _target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {target!r})\n"
        + f"    raise SystemExit(main(_target, {variant!r}, sys.argv[1:]))\n"
    )


def build_variant(reference: Path, entry_file: str, variant: str, destination: Path) -> Path:
    """Copy ``reference`` to ``destination`` with ``entry_file`` wrapped by the variant shim."""
    entry = reference / entry_file
    if not entry.is_file():
        raise FileNotFoundError(f"reference {reference} has no entry file {entry_file}")
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(reference, destination, symlinks=True)
    (destination / entry_file).rename(destination / original_name(entry_file))
    (destination / entry_file).write_text(shim_source(entry_file, variant))
    return destination
