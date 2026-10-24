"""Tree-sitter symbol rows with complexity, SLOC and lineage hashes (runs in the tool env).

Port of ``slop-code-bench`` ``src/slop_code/metrics/languages/python/symbols.py``
(``get_symbols``) at ``25069ba``, reduced to the fields the scoring core reads:
``name``, ``type`` (function, method, class, variable, type_alias), ``start``
and ``end`` (1-based lines), ``complexity`` (1 + decision points for
functions and methods), ``sloc`` (non-blank, non-comment lines in the span),
``parent_class`` and the ``body_hash``, ``structure_hash`` and
``signature_hash`` used for lineage (R). The symbol universe, names, spans
and the values of these fields are the fork's; its other per-symbol metrics
(statements, expressions, nesting, variables, ...) feed no score and are not
computed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .parser import get_python_parser

if TYPE_CHECKING:
    from tree_sitter import Node

SymbolRow = dict[str, Any]

COMPLEXITY_NODE_TYPES = frozenset(
    {
        "if_statement",
        "elif_clause",
        "for_statement",
        "while_statement",
        "except_clause",
        "assert_statement",
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
        "boolean_operator",
        "conditional_expression",
        "if_clause",
    }
)


def _decode_node_text(node: Node) -> str:
    text = node.text
    if text is None:
        raise ValueError("Tree-sitter node text is unavailable")
    return text.decode("utf-8")


def _count_complexity(node: Node) -> int:
    complexity = 0
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in COMPLEXITY_NODE_TYPES:
            complexity += 1
        stack.extend(current.children)
    return complexity


def _count_sloc(node: Node) -> int:
    """Count non-blank, non-comment lines inside a symbol span."""
    sloc = 0
    for raw_line in _decode_node_text(node).splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#"):
            sloc += 1
    return sloc


def _get_block_child(node: Node) -> Node | None:
    for child in node.children:
        if child.type == "block":
            return child
    return None


def _get_name_from_node(node: Node) -> str | None:
    for child in node.children:
        if child.type in {"identifier", "name"}:
            return _decode_node_text(child)
        if child.type == "type" and node.type == "type_alias_statement":
            for subchild in child.children:
                if subchild.type == "identifier":
                    return _decode_node_text(subchild)
    return None


def _get_assignment_name(node: Node) -> str | None:
    target = node.child_by_field_name("left")
    if target is None:
        return None
    if target.type in {"identifier", "name"}:
        return _decode_node_text(target)
    return None


def _field_text(node: Node, field: str) -> str | None:
    child = node.child_by_field_name(field)
    return _decode_node_text(child) if child else None


def _typed_parameter(child: Node) -> tuple[str | None, str | None]:
    name_node = child.child_by_field_name("name")
    if name_node is None:
        name_node = next((sub for sub in child.children if sub.type == "identifier"), None)
    name = _decode_node_text(name_node) if name_node else None
    return name, _field_text(child, "type")


def _splat_parameter(prefix: str) -> Callable[[Node], tuple[str | None, str | None]]:
    def parameter(child: Node) -> tuple[str | None, str | None]:
        for subchild in child.children:
            if subchild.type == "identifier":
                return prefix + _decode_node_text(subchild), None
        return None, None

    return parameter


_PARAMETERS: dict[str, Callable[[Node], tuple[str | None, str | None]]] = {
    "identifier": lambda child: (_decode_node_text(child), None),
    "typed_parameter": _typed_parameter,
    "default_parameter": lambda child: (_field_text(child, "name"), None),
    "typed_default_parameter": lambda child: (
        _field_text(child, "name"),
        _field_text(child, "type"),
    ),
    "list_splat_pattern": _splat_parameter("*"),
    "dictionary_splat_pattern": _splat_parameter("**"),
}


def _extract_signature(node: Node) -> tuple[dict[str, str | None], str | None]:
    """Parameter names mapped to their annotations, and the return annotation."""
    signature: dict[str, str | None] = {}
    params_node = node.child_by_field_name("parameters")
    if params_node:
        for child in params_node.named_children:
            reader = _PARAMETERS.get(child.type)
            param_name, param_type = reader(child) if reader else (None, None)
            if param_name:
                signature[param_name] = param_type
    return signature, _field_text(node, "return_type")


def _compute_body_hash(node: Node) -> str:
    """SHA-256 of the body with identifiers replaced by positional placeholders."""
    block = _get_block_child(node)
    if block is None:
        return hashlib.sha256(b"").hexdigest()
    identifier_map: dict[str, str] = {}
    parts: list[str] = []
    stack: list[Node] = [block]
    while stack:
        current = stack.pop()
        if current.type == "identifier":
            name = _decode_node_text(current)
            if name not in identifier_map:
                identifier_map[name] = f"VAR_{len(identifier_map)}"
            parts.append(identifier_map[name])
        elif current.type in {"string", "integer", "float"}:
            parts.append(_decode_node_text(current))
        else:
            parts.append(current.type)
        stack.extend(reversed(current.children))
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()


def _compute_structure_hash(node: Node) -> str:
    """SHA-256 of the body's node-type tree only (depth-first, with depths)."""
    block = _get_block_child(node)
    if block is None:
        return hashlib.sha256(b"").hexdigest()
    parts: list[str] = []

    def traverse(current: Node, depth: int) -> None:
        parts.append(f"{depth}:{current.type}")
        for child in current.children:
            traverse(child, depth + 1)

    traverse(block, 0)
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _compute_signature_hash(signature: dict[str, str | None], return_type: str | None) -> str:
    """SHA-256 of parameter count, annotated positions (by sorted name) and return flag."""
    sorted_params = sorted(signature.items())
    typed_positions = [str(i) for i, (_, type_ann) in enumerate(sorted_params) if type_ann]
    canonical = (
        f"{len(signature)}:"
        f"{','.join(typed_positions) if typed_positions else 'none'}:"
        f"{'1' if return_type else '0'}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row(node: Node, name: str, symbol_type: str, *, base: bool, **extra: Any) -> SymbolRow:
    complexity = _count_complexity(node)
    return {
        "name": name,
        "type": symbol_type,
        "start": node.start_point[0] + 1,
        "end": node.end_point[0] + 1,
        "complexity": (1 + complexity) if base else complexity,
        "sloc": _count_sloc(node),
        "parent_class": None,
        "body_hash": None,
        "structure_hash": None,
        "signature_hash": None,
        **extra,
    }


class _Extractor:
    def __init__(self) -> None:
        self.rows: list[SymbolRow] = []

    def function(self, node: Node, parent_class: str | None) -> None:
        name = _get_name_from_node(node)
        if not name:
            return
        signature, return_type = _extract_signature(node)
        self.rows.append(
            _row(
                node,
                name,
                "method" if parent_class else "function",
                base=True,
                parent_class=parent_class,
                body_hash=_compute_body_hash(node),
                structure_hash=_compute_structure_hash(node),
                signature_hash=_compute_signature_hash(signature, return_type),
            )
        )
        self.walk(node, None, at_module_level=False)

    def klass(self, node: Node) -> None:
        name = _get_name_from_node(node)
        if not name:
            return
        self.rows.append(_row(node, name, "class", base=False))
        self.walk(node, name, at_module_level=False)

    def assignment(self, node: Node) -> None:
        assign_node = node
        if node.type == "expression_statement":
            found = next((sub for sub in node.children if sub.type == "assignment"), None)
            if found is None:
                return
            assign_node = found
        name = _get_assignment_name(assign_node)
        if name:
            self.rows.append(
                {
                    **_row(node, name, "variable", base=False),
                    "complexity": 0,
                    "sloc": 0,
                }
            )

    def type_alias(self, node: Node) -> None:
        name = _get_name_from_node(node)
        if name:
            self.rows.append(_row(node, name, "type_alias", base=False))

    def walk(self, node: Node, parent_class: str | None, *, at_module_level: bool) -> None:
        for child in node.children:
            kind = child.type
            if kind == "function_definition":
                self.function(child, parent_class)
            elif kind == "class_definition":
                self.klass(child)
            elif kind == "decorated_definition":
                self.walk(child, parent_class, at_module_level=at_module_level)
            elif kind in ("assignment", "expression_statement") and at_module_level:
                self.assignment(child)
            elif kind == "type_alias_statement" and at_module_level:
                self.type_alias(child)
            elif kind == "block":
                self.walk(child, parent_class, at_module_level=False)


def get_symbols(source: Path) -> list[SymbolRow]:
    """Every symbol in a Python file, in the fork's traversal order."""
    code = source.read_text(encoding="utf-8")
    if not code.strip():
        return []
    tree = get_python_parser().parse(code.encode("utf-8"))
    extractor = _Extractor()
    extractor.walk(tree.root_node, None, at_module_level=True)
    return extractor.rows
