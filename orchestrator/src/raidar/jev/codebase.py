"""Whole-codebase state for codebase-scope rubric criteria (plan H3).

Seven SCB criteria (``lonely_interface``, ``one_subclass_hierarchy``,
``strategy_never_varies``, ``interface_per_class``, ``parallel_small_collection``,
``lazy_always_accessed``, ``implicit_default_parameters``) are about how a
symbol is used across the codebase, which its own source and file cannot
show. ``CodebaseIndex`` is built once per checkpoint from every production
Python file (the assessor-owned inventory) with ``ast`` only, and
``CodebaseIndex.state`` renders, for one symbol, a bounded text state with two
sections:

- ``focus``: what the criteria need about this symbol. For a class: its bases,
  direct subclasses, whether it is abstract (ABC, Protocol or an
  ``abstractmethod``), the implementers of each abstract base it has, where it
  is instantiated or passed by name, and the call or access sites of each of
  its methods and properties. For a function or method: its signature and every
  call site with the arguments passed. For the ``<module>`` pseudo-symbol: the
  uses of each module-level name.
- ``inventory``: every module with its classes (bases, abstract marker,
  methods, properties) and functions (signatures), i.e. the module, class and
  function inventory and the inheritance and interface graph.

Sites are matched by name (``ast`` has no types), so a site list can include a
same-named symbol elsewhere; each site names its file and caller so the
question can be judged with that in mind. ``focus`` gets up to half the
budget and ``inventory`` the rest; any clipping makes the input ``partial``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property

from raidar.jev.redaction import Clipped, clip
from raidar.jev.symbols import MODULE_QUALNAME, Symbol, SymbolKind

SITE_CHARS = 160
SITES_PER_NAME = 25
_ABSTRACT_BASES = frozenset({"ABC", "Protocol", "ABCMeta"})
_PROPERTY_DECORATORS = frozenset({"property", "cached_property", "lazy_property"})


@dataclass(frozen=True)
class Site:
    path: str
    caller: str
    line: int
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line} in {self.caller}: {self.text}"


@dataclass(frozen=True)
class ClassInfo:
    path: str
    qualname: str
    bases: tuple[str, ...]
    methods: tuple[str, ...]
    properties: tuple[str, ...]
    abstract: bool

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    def render(self) -> str:
        bases = f"({', '.join(self.bases)})" if self.bases else ""
        marker = " [abstract]" if self.abstract else ""
        parts = [f"class {self.qualname}{bases}{marker}"]
        if self.methods:
            parts.append(f"methods: {', '.join(self.methods)}")
        if self.properties:
            parts.append(f"properties: {', '.join(self.properties)}")
        return "; ".join(parts)


@dataclass(frozen=True)
class FunctionInfo:
    path: str
    qualname: str
    signature: str

    def render(self) -> str:
        return f"def {self.qualname}({self.signature})"


def _short(node: ast.AST) -> str:
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= SITE_CHARS else text[: SITE_CHARS - 1] + "…"


def _last(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {_last(ast.unparse(item).split("(", 1)[0]) for item in node.decorator_list}


def _metaclass(node: ast.ClassDef) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == "metaclass":
            return _last(ast.unparse(keyword.value))
    return None


def _is_abstract(
    node: ast.ClassDef, bases: tuple[str, ...], defs: list[ast.FunctionDef | ast.AsyncFunctionDef]
) -> bool:
    """An ABC or Protocol base, an ABCMeta metaclass, or an ``abstractmethod``."""
    return (
        any(_last(base) in _ABSTRACT_BASES for base in bases)
        or _metaclass(node) in _ABSTRACT_BASES
        or any("abstractmethod" in _decorators(item) for item in defs)
    )


@dataclass
class _Collector(ast.NodeVisitor):
    path: str
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    calls: dict[str, list[Site]] = field(default_factory=lambda: defaultdict(list))
    refs: dict[str, list[Site]] = field(default_factory=lambda: defaultdict(list))
    module_names: list[str] = field(default_factory=list)
    _scope: list[str] = field(default_factory=list)
    _call_funcs: set[int] = field(default_factory=set)

    @property
    def caller(self) -> str:
        return ".".join(self._scope) or MODULE_QUALNAME

    def _site(self, node: ast.expr, text_node: ast.AST) -> Site:
        return Site(self.path, self.caller, node.lineno, _short(text_node))

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            targets: list[ast.expr] = []
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            self.module_names += [t.id for t in targets if isinstance(t, ast.Name)]
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = ".".join([*self._scope, node.name])
        bases = tuple(ast.unparse(base) for base in node.bases)
        defs = [
            item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        properties = tuple(item.name for item in defs if _decorators(item) & _PROPERTY_DECORATORS)
        abstract = _is_abstract(node, bases, defs)
        methods = tuple(item.name for item in defs if item.name not in properties)
        self.classes.append(ClassInfo(self.path, qualname, bases, methods, properties, abstract))
        self._nested(node.name, node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._scope, node.name])
        self.functions.append(FunctionInfo(self.path, qualname, ast.unparse(node.args)))
        self._nested(node.name, node)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def _nested(self, name: str, node: ast.AST) -> None:
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name:
            self.calls[name].append(self._site(node, node))
            self._call_funcs.add(id(func))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if id(node) not in self._call_funcs and isinstance(node.ctx, ast.Load):
            self.refs[node.attr].append(self._site(node, node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if id(node) not in self._call_funcs and isinstance(node.ctx, ast.Load):
            self.refs[node.id].append(self._site(node, node))


@dataclass(frozen=True)
class CodebaseState:
    sections: dict[str, Clipped]

    @property
    def clipped(self) -> bool:
        return any(text.clipped for text in self.sections.values())

    def as_state(self) -> dict[str, str]:
        return {name: text.text for name, text in self.sections.items()}


class CodebaseIndex:
    """Modules, classes, functions and name-matched usage sites of one snapshot."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.classes: list[ClassInfo] = []
        self.functions: list[FunctionInfo] = []
        self.calls: dict[str, list[Site]] = defaultdict(list)
        self.refs: dict[str, list[Site]] = defaultdict(list)
        self.module_names: dict[str, list[str]] = {}
        for path, text in sorted(files.items()):
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue  # the rubric reports the parse error; nothing to index
            collector = _Collector(path)
            collector.visit(tree)
            self.classes += collector.classes
            self.functions += collector.functions
            for name, sites in collector.calls.items():
                self.calls[name] += sites
            for name, sites in collector.refs.items():
                self.refs[name] += sites
            self.module_names[path] = collector.module_names
        self.subclasses: dict[str, list[ClassInfo]] = defaultdict(list)
        for info in self.classes:
            for base in info.bases:
                self.subclasses[_last(base)].append(info)
        self._classes = {(item.path, item.qualname): item for item in self.classes}
        self._functions = {(item.path, item.qualname): item for item in self.functions}

    @cached_property
    def inventory_text(self) -> str:
        return self.inventory()

    def inventory(self) -> str:
        lines = []
        for path in sorted(self.module_names):
            lines.append(f"{path}:")
            lines += [f"  {item.render()}" for item in self.classes if item.path == path]
            lines += [
                f"  {item.render()}"
                for item in self.functions
                if item.path == path and "." not in item.qualname
            ]
        return "\n".join(lines)

    def _sites(self, title: str, sites: Iterable[Site]) -> list[str]:
        found = list(sites)
        lines = [f"{title}: {len(found)} site(s)"]
        lines += [f"  {site.render()}" for site in found[:SITES_PER_NAME]]
        if len(found) > SITES_PER_NAME:
            lines.append(f"  … {len(found) - SITES_PER_NAME} more")
        return lines

    def _class_focus(self, symbol: Symbol) -> list[str]:
        info = self._classes.get(symbol.identity)
        if info is None:
            return [f"class {symbol.qualname} was not indexed"]
        lines = [info.render()]
        subclasses = self.subclasses.get(info.name, [])
        lines.append(
            "direct subclasses: "
            + (", ".join(f"{c.path}::{c.qualname}" for c in subclasses) or "none")
        )
        for base in info.bases:
            peers = self.subclasses.get(_last(base), [])
            lines.append(
                f"implementers of base {base}: "
                + ", ".join(f"{c.path}::{c.qualname}" for c in peers)
            )
        lines += self._sites(f"instantiations of {info.name}", self.calls.get(info.name, ()))
        lines += self._sites(f"references to {info.name}", self.refs.get(info.name, ()))
        for method in info.methods:
            lines += self._sites(f"calls of .{method}", self.calls.get(method, ()))
        for prop in info.properties:
            lines += self._sites(f"accesses of .{prop}", self.refs.get(prop, ()))
        return lines

    def _function_focus(self, symbol: Symbol) -> list[str]:
        info = self._functions.get(symbol.identity)
        name = _last(symbol.qualname)
        lines = [info.render() if info else f"def {symbol.qualname}(…)"]
        lines += self._sites(f"calls of {name}", self.calls.get(name, ()))
        lines += self._sites(f"references to {name}", self.refs.get(name, ()))
        return lines

    def _module_focus(self, symbol: Symbol) -> list[str]:
        lines = []
        for name in self.module_names.get(symbol.path, []):
            lines += self._sites(f"uses of {name}", self.refs.get(name, ()))
        return lines or ["no module-level names"]

    def focus(self, symbol: Symbol) -> str:
        if symbol.kind is SymbolKind.CLASS:
            lines = self._class_focus(symbol)
        elif symbol.kind is SymbolKind.MODULE:
            lines = self._module_focus(symbol)
        else:
            lines = self._function_focus(symbol)
        return "\n".join(lines)

    def state(self, symbol: Symbol, budget: int) -> CodebaseState:
        """The bounded codebase sections for ``symbol`` within ``budget`` characters."""
        focus = clip(self.focus(symbol), budget // 2)
        inventory = clip(self.inventory_text, budget - len(focus.text))
        return CodebaseState({"focus": focus, "inventory": inventory})
