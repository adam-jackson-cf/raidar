"""Entrypoint-bounded call graph for architecture (H) (runs in the tool env).

Port of ``slop-code-bench`` ``src/slop_code/metrics/languages/python/graph.py``
(``build_dependency_graph`` and ``dependency_graph_traversal``) at
``25069ba``. Nodes are ``<path>::<Class.method|function>`` for every function
in the files reachable from the entrypoint; an edge counts the resolved calls
from caller to callee (same file first, then imports, local constructor
types, ``self``/``super()`` through the class hierarchy, then a unique name).

Two deliberate differences, neither of which changes a resolved edge:

- Files are visited in sorted order. The fork iterates a ``set`` of paths,
  whose order follows the per-process string hash seed, so where a call has
  several equally ranked candidates in different files the fork's choice is
  not reproducible between runs; sorted order makes it deterministic.
- Each traversal node also reports its definition's first and last line,
  used only to point H evidence at ``file:line``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import networkx as nx

from .imports import trace_source_files
from .parser import get_python_parser

if TYPE_CHECKING:
    from tree_sitter import Node

FunctionIndex = dict[str, list[tuple[Path, str]]]


def _text(source: bytes, node: Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _extract_functions_from_file(file_path: Path) -> list[tuple[str, Node]]:
    """``(qualified_name, node)`` for each function and direct class method."""
    try:
        source = file_path.read_bytes()
    except OSError:
        return []
    tree = get_python_parser().parse(source)
    functions: list[tuple[str, Node]] = []

    def visit(node: Node, class_name: str | None = None) -> None:
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _text(source, name_node)
                functions.append((f"{class_name}.{name}" if class_name else name, node))
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            body_node = node.child_by_field_name("body")
            if name_node and body_node:
                for child in body_node.children:
                    visit(child, _text(source, name_node))
        else:
            for child in node.children:
                visit(child, class_name)

    visit(tree.root_node)
    return functions


def _extract_class_hierarchy(file_path: Path) -> dict[str, list[str]]:
    """Class name mapped to its simple-identifier base classes."""
    try:
        source = file_path.read_bytes()
    except OSError:
        return {}
    tree = get_python_parser().parse(source)
    hierarchy: dict[str, list[str]] = {}

    def visit(node: Node) -> None:
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                hierarchy[_text(source, name_node)] = [
                    _text(source, arg)
                    for child in node.children
                    if child.type == "argument_list"
                    for arg in child.children
                    if arg.type == "identifier"
                ]
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    return hierarchy


def _alias(source: bytes, child: Node) -> tuple[str, str] | None:
    name_node = child.child_by_field_name("name")
    alias_node = child.child_by_field_name("alias")
    if name_node and alias_node:
        return _text(source, name_node), _text(source, alias_node)
    return None


class _ImportMap:
    """The fork's graph-local import map: imported name -> module (path stem for relatives)."""

    def __init__(self, source: bytes, file_rel_path: Path | None) -> None:
        self.source = source
        self.file_rel_path = file_rel_path
        self.imports: dict[str, str] = {}

    def resolve_relative(self, level: int, module_name: str | None) -> str | None:
        if self.file_rel_path is None:
            return None
        current = self.file_rel_path.parent
        for _ in range(level - 1):
            current = current.parent
            if current == Path():
                return None
        if module_name:
            return (current / "/".join(module_name.split("."))).as_posix()
        return current.as_posix()

    def import_statement(self, node: Node) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                module_name = _text(self.source, child)
                self.imports[module_name] = module_name
            elif child.type == "aliased_import":
                pair = _alias(self.source, child)
                if pair:
                    self.imports[pair[1]] = pair[0]

    def _names(self, node: Node, module_node: Node, target: str, *, sibling: bool) -> None:
        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                name = _text(self.source, child)
                self.imports[name] = f"{target}/{name}" if sibling else target
            elif child.type == "aliased_import":
                pair = _alias(self.source, child)
                if pair:
                    self.imports[pair[1]] = f"{target}/{pair[0]}" if sibling else target

    def import_from(self, node: Node) -> None:
        module_node = node.child_by_field_name("module_name")
        if not module_node:
            return
        if module_node.type != "relative_import":
            self._names(node, module_node, _text(self.source, module_node), sibling=False)
            return
        level, inner = 0, None
        for child in module_node.children:
            if child.type == "import_prefix":
                level = child.end_byte - child.start_byte
            elif child.type == "dotted_name":
                inner = _text(self.source, child)
        base = self.resolve_relative(level, inner)
        if base is not None:
            self._names(node, module_node, base, sibling=inner is None)

    def visit(self, node: Node) -> None:
        if node.type == "import_statement":
            self.import_statement(node)
        elif node.type == "import_from_statement":
            self.import_from(node)
        for child in node.children:
            self.visit(child)


def _extract_imports(file_path: Path, file_rel_path: Path | None = None) -> dict[str, str]:
    try:
        source = file_path.read_bytes()
    except OSError:
        return {}
    mapping = _ImportMap(source, file_rel_path)
    mapping.visit(get_python_parser().parse(source).root_node)
    return mapping.imports


def _call_target(node: Node, source: bytes) -> tuple[str, str | None] | None:
    func_node = node.child_by_field_name("function")
    if not func_node:
        return None
    if func_node.type == "identifier":
        return _text(source, func_node), None
    if func_node.type == "attribute":
        object_node = func_node.child_by_field_name("object")
        attr_node = func_node.child_by_field_name("attribute")
        if object_node and attr_node:
            return _text(source, attr_node), _text(source, object_node)
    return None


def _extract_function_calls(func_node: Node, source: bytes) -> list[tuple[str, str | None]]:
    """``(called_name, qualifier)`` for every call in the function body."""
    calls: list[tuple[str, str | None]] = []

    def visit(node: Node) -> None:
        if node.type == "call":
            target = _call_target(node, source)
            if target is not None:
                calls.append(target)
        for child in node.children:
            visit(child)

    body = func_node.child_by_field_name("body")
    if body:
        visit(body)
    return calls


def _assignment_parts(node: Node) -> tuple[Node | None, Node | None]:
    """The first identifier target and the first call after ``=`` (fork order)."""
    children = list(node.children)
    target = None
    value = None
    for i, child in enumerate(children):
        if child.type == "identifier" and target is None:
            target = child
        elif child.type == "=" and target is not None:
            value = next((later for later in children[i + 1 :] if later.type == "call"), value)
    return target, value


def _assignment_type(node: Node, source: bytes) -> tuple[str, str] | None:
    target, value = _assignment_parts(node)
    if not (target and value):
        return None
    func_node = value.child_by_field_name("function")
    if not func_node or func_node.type != "identifier":
        return None
    class_name = _text(source, func_node)
    if class_name and class_name[0].isupper():
        return _text(source, target), class_name
    return None


def _extract_local_types(func_node: Node, source: bytes) -> dict[str, str]:
    """Local variable -> class name, from ``var = ClassName(...)`` assignments."""
    local_types: dict[str, str] = {}

    def visit(node: Node) -> None:
        if node.type == "assignment":
            found = _assignment_type(node, source)
            if found is not None:
                local_types[found[0]] = found[1]
        for child in node.children:
            visit(child)

    body = func_node.child_by_field_name("body")
    if body:
        visit(body)
    return local_types


def _node_id(file_path: Path, qual_name: str) -> str:
    return f"{file_path.as_posix()}::{qual_name}"


def _matches_module(file_path: Path, module_name: str) -> bool:
    return file_path.stem == module_name or file_path.with_suffix("").as_posix() == module_name


class _CallResolver:
    """The fork's ``_resolve_call`` priority rules for one caller file."""

    def __init__(
        self,
        caller_file: Path,
        imports: dict[str, str],
        index: FunctionIndex,
        hierarchy: dict[str, list[str]],
    ) -> None:
        self.caller_file = caller_file
        self.imports = imports
        self.index = index
        self.hierarchy = hierarchy

    def resolve(
        self, called: str, qualifier: str | None, local_types: dict[str, str], caller: str
    ) -> str | None:
        candidates = self.index.get(called, [])
        if not candidates:
            return None
        if qualifier:
            return self._qualified(called, qualifier, candidates, local_types, caller)
        return self._unqualified(called, candidates)

    def _first(self, candidates: list[tuple[Path, str]], wanted: str) -> str | None:
        return next((_node_id(path, name) for path, name in candidates if name == wanted), None)

    def _self_call(
        self, called: str, candidates: list[tuple[Path, str]], caller: str
    ) -> str | None:
        current = caller.rsplit(".", 1)[0]
        for class_name in (current, *self.hierarchy.get(current, [])):
            expected = f"{class_name}.{called}"
            for file_path, qual_name in candidates:
                if file_path == self.caller_file and qual_name == expected:
                    return _node_id(file_path, qual_name)
        return None

    def _super_call(
        self, called: str, candidates: list[tuple[Path, str]], caller: str
    ) -> str | None:
        parents = self.hierarchy.get(caller.split(".")[0], [])
        return self._first(candidates, f"{parents[0]}.{called}") if parents else None

    def _imported(self, qualifier: str, candidates: list[tuple[Path, str]]) -> str | None:
        if qualifier not in self.imports:
            return None
        module_name = self.imports[qualifier]
        return next(
            (
                _node_id(path, name)
                for path, name in candidates
                if _matches_module(path, module_name)
            ),
            None,
        )

    def _qualified(
        self,
        called: str,
        qualifier: str,
        candidates: list[tuple[Path, str]],
        local_types: dict[str, str],
        caller: str,
    ) -> str | None:
        in_method = bool(caller) and "." in caller
        found = None
        if qualifier == "self" and in_method:
            found = self._self_call(called, candidates, caller)
        if found is None and qualifier == "super()" and in_method:
            found = self._super_call(called, candidates, caller)
        if found is None:
            found = self._imported(qualifier, candidates)
        if found is None and qualifier in local_types:
            found = self._first(candidates, f"{local_types[qualifier]}.{called}")
        return found

    def _unqualified(self, called: str, candidates: list[tuple[Path, str]]) -> str | None:
        for file_path, qual_name in candidates:
            if file_path == self.caller_file:
                return _node_id(file_path, qual_name)
        if called in self.imports:
            module_name = self.imports[called]
            for file_path, qual_name in candidates:
                named = qual_name.endswith(f".{called}") or qual_name == called
                if _matches_module(file_path, module_name) and named:
                    return _node_id(file_path, qual_name)
        if len(candidates) == 1:
            return _node_id(*candidates[0])
        return None


def build_dependency_graph(snapshot_dir: Path, entrypoint: Path) -> nx.DiGraph:
    """Weighted call graph over the files reachable from ``entrypoint``."""
    snapshot_dir = snapshot_dir.resolve()
    entrypoint_abs = (
        entrypoint if entrypoint.is_absolute() else (snapshot_dir / entrypoint).resolve()
    )
    if not entrypoint_abs.exists():
        return nx.DiGraph()
    try:
        source_files = sorted(trace_source_files(entrypoint_abs, snapshot_dir))
    except Exception:  # the fork logs and returns an empty graph
        return nx.DiGraph()
    graph = nx.DiGraph()
    functions: dict[Path, list[tuple[str, Node]]] = {}
    index: FunctionIndex = {}
    for file_path in source_files:
        functions[file_path] = _extract_functions_from_file(snapshot_dir / file_path)
        for qual_name, node in functions[file_path]:
            graph.add_node(
                _node_id(file_path, qual_name),
                line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
            )
            index.setdefault(qual_name.split(".")[-1], []).append((file_path, qual_name))
    for file_path, defined in functions.items():
        _add_call_edges(graph, snapshot_dir, file_path, defined, index)
    return graph


def _add_call_edges(
    graph: nx.DiGraph,
    snapshot_dir: Path,
    file_path: Path,
    defined: list[tuple[str, Node]],
    index: FunctionIndex,
) -> None:
    absolute = snapshot_dir / file_path
    resolver = _CallResolver(
        file_path, _extract_imports(absolute, file_path), index, _extract_class_hierarchy(absolute)
    )
    source = absolute.read_bytes()
    for qual_name, func_node in defined:
        local_types = _extract_local_types(func_node, source)
        calls: Counter[tuple[str, str | None]] = Counter(_extract_function_calls(func_node, source))
        for (called, qualifier), count in calls.items():
            callee = resolver.resolve(called, qualifier, local_types, qual_name)
            if callee:
                graph.add_edge(_node_id(file_path, qual_name), callee, weight=count)


def dependency_graph_traversal(graph: nx.DiGraph) -> dict[str, list[dict[str, Any]]]:
    """Nodes and weighted edges in UTF-8 byte order, as the fork's traversal emits them."""
    nodes = [
        {
            "identity": node,
            "path": node.partition("::")[0],
            "line": graph.nodes[node].get("line"),
            "end_line": graph.nodes[node].get("end_line"),
        }
        for node in sorted(graph.nodes(), key=lambda item: item.encode("utf-8"))
    ]
    edges = [
        {"source": source, "target": target, "weight": data.get("weight", 1)}
        for source, target, data in sorted(
            graph.edges(data=True),
            key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
        )
    ]
    return {"nodes": nodes, "edges": edges}
