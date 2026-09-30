"""Digests of Python source that change only when the code does (N3).

A module is hashed as its syntax tree, not its bytes: comments, blank lines,
layout, quoting style and docstrings (every string-only statement, which
Python evaluates and discards) do not reach the digest, while any change to a
statement, expression, name, literal, import, decorator or annotation does.
The tree is serialised field by field with ``None`` and empty-list fields
omitted, so the digest is the same on every supported Python version
(``ast.dump`` itself changed its output in 3.13). Line and column positions
are attributes, not fields, and are never read.

Scorer implementation digests (``raidar.scorers.catalog``) and the lock's
``raidar_spec`` toolchain digest use this, so a docstring or comment edit
never invalidates stored Assessments or a lock.
"""

from __future__ import annotations

import ast
from pathlib import Path

from raidar.spec.canonical import digest_json

SOURCE_DIGEST_SCHEME = "python-ast/1"
"""Recorded beside every source digest; bump it when the normalisation changes."""

_BODY_FIELDS = ("body", "orelse", "finalbody")
_REQUIRES_BODY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_IGNORED_FIELDS = frozenset({"type_comment", "kind"})
"""``type_comment`` is a comment; ``Constant.kind`` only records a ``u''`` prefix."""


def _is_string_statement(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _strip_string_statements(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for name in _BODY_FIELDS:
            body = getattr(node, name, None)
            if not isinstance(body, list):
                continue
            kept = [statement for statement in body if not _is_string_statement(statement)]
            if not kept and body and name == "body" and isinstance(node, _REQUIRES_BODY):
                kept = [ast.Pass()]
            setattr(node, name, kept)


def _scalar(value: object) -> object:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return {"bool": value}
    if isinstance(value, int):
        return {"int": str(value)}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if value is Ellipsis:
        return {"ellipsis": True}
    return {type(value).__name__: repr(value)}


def _canonical(value: object) -> object:
    if isinstance(value, ast.AST):
        fields = {}
        for name in value._fields:
            item = getattr(value, name, None)
            if name in _IGNORED_FIELDS or item is None or item == []:
                continue
            fields[name] = _canonical(item)
        return [type(value).__name__, fields]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return _scalar(value)


def normalised_source(source: bytes | str, *, filename: str = "<source>") -> object:
    """The JSON-able normal form of ``source`` that ``python_source_digest`` hashes."""
    tree = ast.parse(source, filename=filename)
    _strip_string_statements(tree)
    return _canonical(tree)


def python_source_digest(path: Path) -> str:
    """sha256 of the normalised syntax tree of the Python file at ``path``."""
    return digest_json(
        {
            "scheme": SOURCE_DIGEST_SCHEME,
            "tree": normalised_source(path.read_bytes(), filename=str(path)),
        }
    )
