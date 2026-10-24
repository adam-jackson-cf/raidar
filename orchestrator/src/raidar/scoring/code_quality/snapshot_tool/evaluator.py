"""Evaluator-interpreter token and AST semantics for V/E (runs in the pinned tool env).

Port of the program the SCB fork passes to its evaluator interpreter with
``python -c`` (``metrics/scoring/production_quality.py::_verified_payload`` at
``25069ba``), restructured into functions with identical behaviour:

- the interpreter identity and ``parser_tokenizer_schema_id`` (a digest of
  the implementation, version, cache tag, ``ast``/``tokenize`` sources and the
  probe's tokens and AST), so evidence from a different parser is detectable;
- per-file source lines: every line a non-layout, non-comment token spans;
- clone groups: maximal repeated vectors of at least six sibling statements
  (compared by ``ast.dump`` without positions), with non-overlapping
  occurrences per path.

Only the standard library is used, and nothing from Raidar is imported.
"""

from __future__ import annotations

import ast
import hashlib
import sys
import tokenize
from collections.abc import Iterable
from io import BytesIO
from typing import Any

MIN_STATEMENTS = 6
PROBE = b"x = 1\nif x:\n    y = 'z'\n"
_LAYOUT_TOKENS = frozenset(
    {"ENCODING", "ENDMARKER", "NEWLINE", "NL", "INDENT", "DEDENT", "COMMENT"}
)

Occurrence = tuple[str, int, int]
Span = tuple[str, int, int]
StatementData = list[tuple[tuple[str, ...], tuple[tuple[int, int], ...]]]


class EvaluatorInputError(ValueError):
    """A source file cannot be tokenized or parsed by the evaluator interpreter."""


def digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _dump(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def parser_tokenizer_schema_id() -> str:
    tokens = repr(
        [
            (token.type, token.string, token.start, token.end)
            for token in tokenize.tokenize(BytesIO(PROBE).readline)
        ]
    )
    parts = (
        sys.implementation.name,
        sys.version,
        sys.implementation.cache_tag or "",
        digest(ast.__file__),
        digest(tokenize.__file__),
        PROBE.hex(),
        tokens,
        _dump(ast.parse(PROBE)),
    )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def interpreter_identity() -> dict[str, str]:
    return {
        "executable": sys.executable,
        "implementation": sys.implementation.name,
        "version": sys.version,
        "cache_tag": sys.implementation.cache_tag or "",
        "executable_sha256": digest(sys.executable),
        "ast_sha256": digest(ast.__file__),
        "tokenize_sha256": digest(tokenize.__file__),
    }


def loc(data: bytes) -> list[int]:
    lines: set[int] = set()
    for token in tokenize.tokenize(BytesIO(data).readline):
        if tokenize.tok_name[token.type] not in _LAYOUT_TOKENS:
            lines.update(range(token.start[0], token.end[0] + 1))
    return sorted(line for line in lines if line > 0)


def stmt_lists(node: ast.AST) -> list[list[ast.stmt]]:
    result = []
    for parent in ast.walk(node):
        for _, value in ast.iter_fields(parent):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                result.append(value)
    return result


def index_candidates(
    path: str, data: bytes, index: dict[bytes, list[tuple[str, int, int]]]
) -> None:
    for list_id, statements in enumerate(stmt_lists(ast.parse(data))):
        tokens = [hashlib.sha256(_dump(statement).encode()).digest() for statement in statements]
        for start in range(len(tokens) - MIN_STATEMENTS + 1):
            key = hashlib.sha256(b"\0".join(tokens[start : start + MIN_STATEMENTS])).digest()
            index.setdefault(key, []).append((path, list_id, start))


def statement_data(path: str, data: bytes, cache: dict[str, StatementData]) -> StatementData:
    if path not in cache:
        cache[path] = [
            (
                tuple(_dump(statement) for statement in statements),
                tuple((statement.lineno, statement.end_lineno or 0) for statement in statements),
            )
            for statements in stmt_lists(ast.parse(data))
        ]
    return cache[path]


class _CloneSearch:
    """Extends every repeated six-statement window to its maximal shared vector."""

    def __init__(self, sources: dict[str, bytes]) -> None:
        self.sources = sources
        self.cache: dict[str, StatementData] = {}
        self.candidates: dict[tuple[str, ...], set[Span]] = {}

    def _data(self, path: str, list_id: int) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
        return statement_data(path, self.sources[path], self.cache)[list_id]

    def add_window(self, occurrences: list[tuple[str, int, int]]) -> None:
        repeated: dict[tuple[str, ...], list[tuple[str, int, int]]] = {}
        for path, list_id, start in occurrences:
            values, _ = self._data(path, list_id)
            repeated.setdefault(values[start : start + MIN_STATEMENTS], []).append(
                (path, list_id, start)
            )
        for records in repeated.values():
            for first in range(len(records)):
                for second in range(first + 1, len(records)):
                    self._extend(records[first], records[second])

    def _extend(self, left: tuple[str, int, int], right: tuple[str, int, int]) -> None:
        left_path, left_list, left_start = left
        right_path, right_list, right_start = right
        left_values, left_spans = self._data(left_path, left_list)
        right_values, right_spans = self._data(right_path, right_list)
        prefix = 0
        while (
            left_start - prefix - 1 >= 0
            and right_start - prefix - 1 >= 0
            and left_values[left_start - prefix - 1] == right_values[right_start - prefix - 1]
        ):
            prefix += 1
        suffix = MIN_STATEMENTS
        while (
            left_start + suffix < len(left_values)
            and right_start + suffix < len(right_values)
            and left_values[left_start + suffix] == right_values[right_start + suffix]
        ):
            suffix += 1
        vector = left_values[left_start - prefix : left_start + suffix]
        left_span = (
            left_path,
            left_spans[left_start - prefix][0],
            left_spans[left_start + suffix - 1][1],
        )
        right_span = (
            right_path,
            right_spans[right_start - prefix][0],
            right_spans[right_start + suffix - 1][1],
        )
        self.candidates.setdefault(vector, set()).update((left_span, right_span))


def _non_overlapping(occurrences: Iterable[Span]) -> list[Span]:
    chosen: list[Span] = []
    for occurrence in sorted(occurrences):
        if all(
            occurrence[0] != old[0] or occurrence[2] < old[1] or old[2] < occurrence[1]
            for old in chosen
        ):
            chosen.append(occurrence)
    return chosen


def _contains(other: tuple[str, ...], vector: tuple[str, ...]) -> bool:
    return len(other) > len(vector) and any(
        other[index : index + len(vector)] == vector
        for index in range(len(other) - len(vector) + 1)
    )


def clone_groups(
    sources: dict[str, bytes], index: dict[bytes, list[tuple[str, int, int]]]
) -> list[dict[str, Any]]:
    search = _CloneSearch(sources)
    for windows in index.values():
        if len(windows) >= 2:
            search.add_window(windows)
    accepted = []
    for vector, spans in search.candidates.items():
        chosen = _non_overlapping(spans)
        if len(chosen) >= 2:
            accepted.append((vector, tuple(chosen)))
    maximal = [
        (vector, occurrences)
        for vector, occurrences in accepted
        if not any(_contains(other, vector) for other, _ in accepted)
    ]
    return [
        {"vector": list(vector), "occurrences": [list(item) for item in occurrences]}
        for vector, occurrences in sorted(maximal, key=lambda pair: (pair[0], pair[1]))
    ]


def evaluate(files: list[dict[str, str]]) -> dict[str, Any]:
    """The fork's evaluator payload for ``files`` (``{path, bytes}`` with hex bytes)."""
    schema = parser_tokenizer_schema_id()
    rows = []
    sources: dict[str, bytes] = {}
    index: dict[bytes, list[tuple[str, int, int]]] = {}
    for item in files:
        data = bytes.fromhex(item["bytes"])
        try:
            source_lines = loc(data)
            index_candidates(item["path"], data, index)
        except Exception as error:  # any tokenizer or parser failure invalidates evidence
            raise EvaluatorInputError(str(error)) from error
        sources[item["path"]] = data
        rows.append(
            {
                "path": item["path"],
                "hash": hashlib.sha256(data).hexdigest(),
                "source_lines": source_lines,
            }
        )
    return {
        "interpreter": interpreter_identity(),
        "parser_tokenizer_probe": PROBE.hex(),
        "parser_tokenizer_schema_id": schema,
        "files": rows,
        "clone_groups": clone_groups(sources, index),
    }
