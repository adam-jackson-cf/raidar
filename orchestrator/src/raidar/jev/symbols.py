"""Python symbols of a production file, the unit Jev rubric questions are asked about.

A symbol is a class, a function or a method (nested functions stay inside
their parent), identified by ``(path, qualname)``. Module-level statements
other than imports, the module docstring and definitions form one ``<module>``
pseudo-symbol, so registries and constants defined at module level are still
judged. Spans are 1-based and inclusive and start at the first decorator.

A file that does not parse yields no symbols and is reported as such; it is
never silently treated as empty.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from enum import StrEnum

MODULE_QUALNAME = "<module>"


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


@dataclass(frozen=True)
class Symbol:
    path: str
    qualname: str
    kind: SymbolKind
    start: int
    end: int
    source: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    @property
    def identity(self) -> tuple[str, str]:
        return (self.path, self.qualname)

    @property
    def line_count(self) -> int:
        return self.end - self.start + 1


class SymbolParseError(ValueError):
    """The file is not valid Python, so it has no symbols to ask about."""


_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_SKIPPED_MODULE_NODES = (*_DEFINITIONS, ast.Import, ast.ImportFrom)


def _start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    lines = [node.lineno, *(decorator.lineno for decorator in decorators)]  # type: ignore[attr-defined]
    return min(lines)


def _text(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])


def _definitions(
    body: list[ast.stmt], lines: list[str], path: str, prefix: str, in_class: bool
) -> list[Symbol]:
    found = []
    for node in body:
        if not isinstance(node, _DEFINITIONS):
            continue
        qualname = f"{prefix}{node.name}"
        end = node.end_lineno or node.lineno
        if isinstance(node, ast.ClassDef):
            kind = SymbolKind.CLASS
        else:
            kind = SymbolKind.METHOD if in_class else SymbolKind.FUNCTION
        start = _start(node)
        found.append(Symbol(path, qualname, kind, start, end, _text(lines, start, end)))
        if isinstance(node, ast.ClassDef):
            found.extend(_definitions(node.body, lines, path, f"{qualname}.", True))
    return found


def _end(node: ast.stmt) -> int:
    return node.end_lineno or node.lineno


def _module_symbol(tree: ast.Module, lines: list[str], path: str) -> Symbol | None:
    body = tree.body[1:] if ast.get_docstring(tree, clean=False) is not None else tree.body
    statements = [node for node in body if not isinstance(node, _SKIPPED_MODULE_NODES)]
    if not statements:
        return None
    parts = [_text(lines, node.lineno, _end(node)) for node in statements]
    start, end = statements[0].lineno, _end(statements[-1])
    return Symbol(path, MODULE_QUALNAME, SymbolKind.MODULE, start, end, "".join(parts))


def extract_symbols(path: str, source: str) -> tuple[Symbol, ...]:
    """Every symbol of one Python file, in source order."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise SymbolParseError(f"{path}: {error}") from error
    lines = source.splitlines(keepends=True)
    symbols = _definitions(tree.body, lines, path, "", False)
    module = _module_symbol(tree, lines, path)
    if module is not None:
        symbols.append(module)
    return tuple(sorted(symbols, key=lambda item: (item.start, item.qualname)))
