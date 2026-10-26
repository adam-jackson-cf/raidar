"""Python source spans read with ``ast``: a test and its helpers, enclosing functions.

``extract(module_text, node_id)`` finds the function a node id names
(``path::test_f``, ``path::Class::test_m``, parameters ignored) and returns it
with line numbers, plus one hop of same-module helpers: module-level functions
and assignments the test names, fixtures by parameter name, methods it calls
on ``self``/``cls`` and its class's ``setUp``/``tearDown``. A test made at
module level by a loop writing ``globals()`` is returned as that loop; for an
aspect judge (``at_import``) a test made when the module is imported (a
``setattr`` or ``exec`` at module or class level too) is returned as that
statement. Such a statement stands in for the test only when a string it (or a
module-level value it names) holds can be the test's name: the name itself,
or a template of it (``f"test_{n}"``, ``"test_" + n``, ``"test_%d"``); any
other test that no ``def`` names is not found. A
helper that is itself a test function is kept (a test that only calls another
test proves nothing of its own, and the judge must see that).

Lines are counted as Python counts them: only a line feed, a carriage return
or the two together end one (``str.splitlines`` also splits on a form feed and
other separators, which would shift every line after them).

``numbered_lines(text)`` reads back the line numbers and text that
``Source.numbered`` wrote (what a state shows of a file); ``imported_modules`` tells whether
a test module parses. Ported from factory-x-loops ``capability/judge/pysource.py`` at
``ac5db0f`` (the parts the aspect judge reads).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypeGuard

_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
_NUMBERED = re.compile(r"^\s*(\d+)[ +] (.*)$")
# The fields of a compound statement that hold its body (not its header).
_BODIES = ("body", "orelse", "finalbody", "handlers", "cases")
# A method's node id names its class and the method: ``Class::method``.
METHOD_PARTS = 2
_CLASS_HOOKS = ("setUp", "setUpClass", "tearDown", "tearDownClass")
# Names whose use at import time makes tests (the gate's ``TESTQ.generated-test``).
_GENERATING = frozenset({"globals", "locals", "vars", "setattr", "exec"})
# A hole in a string template: a ``str.format`` field or a ``%`` conversion.
_HOLE = re.compile(r"\{[^{}]*\}|%[-#0 +]*\d*(?:\.\d+)?[a-zA-Z]")


def source_lines(text: str) -> list[str]:
    """Return ``text``'s lines as ``ast`` numbers them (module docstring)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


@dataclass(frozen=True)
class Source:
    """A span of a file, its lines numbered."""

    name: str
    file: str
    start: int
    end: int
    lines: tuple[str, ...]

    def numbered(self, marked: frozenset[int] = frozenset()) -> str:
        """Return the span with line numbers, ``+`` on each line in ``marked``."""
        return "\n".join(
            f"{number:>5}{'+' if number in marked else ' '} {line}"
            for number, line in enumerate(self.lines, start=self.start)
        )


@dataclass(frozen=True)
class TestSource:
    """A test's source and its one-hop helpers; ``generated`` for a globals() loop."""

    __test__ = False  # not a pytest test class

    source: Source
    helpers: tuple[Source, ...] = ()
    generated: bool = False


@dataclass
class _Module:
    path: str
    text: str
    lines: list[str] = field(default_factory=list)
    tree: ast.Module | None = None
    top: dict[str, ast.stmt] = field(default_factory=dict)
    classes: dict[str, dict[str, ast.stmt]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.lines = source_lines(self.text)
        self.tree = ast.parse(self.text)
        for node in self.tree.body:
            self._index(node)

    def _index(self, node: ast.stmt) -> None:
        """Record a module-level function, class or assignment under its name."""
        if isinstance(node, _FUNCTION):
            self.top[node.name] = node
        elif isinstance(node, ast.ClassDef):
            self.classes[node.name] = {
                item.name: item for item in node.body if isinstance(item, _FUNCTION)
            }
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    self.top.setdefault(target.id, node)

    def source(self, name: str, node: ast.stmt) -> Source:
        decorators = getattr(node, "decorator_list", [])
        start = min([item.lineno for item in decorators] + [node.lineno])
        end = node.end_lineno or node.lineno
        return Source(name, self.path, start, end, tuple(self.lines[start - 1 : end]))

    def generator(self, name: str, at_import: bool = False) -> ast.stmt | None:
        """Return the module-level statement that can make test ``name`` at import.

        By default only a module-level loop that writes ``globals()`` (judge states are
        built on that); ``at_import``: a statement of any kind that uses ``globals``,
        ``setattr``, ``exec``... Either way the first whose strings can be ``name``
        (module docstring); None when none can.
        """
        assert self.tree is not None
        for node in self.tree.body:
            if self._generates(node, at_import) and self._may_name(node, name):
                return node
        return None

    def _generates(self, node: ast.stmt, at_import: bool) -> bool:
        """Whether ``node`` makes tests when the module is imported."""
        if not at_import:
            return isinstance(node, ast.For | ast.While) and any(
                isinstance(item, ast.Name) and item.id == "globals" for item in ast.walk(node)
            )
        if isinstance(node, (*_FUNCTION, ast.Import, ast.ImportFrom)):
            return False
        return any(
            isinstance(item, ast.Name) and item.id in _GENERATING for item in _at_import(node)
        )

    def _may_name(self, node: ast.stmt, name: str) -> bool:
        """Whether a string ``node`` or a module-level value it names holds can be ``name``."""
        named = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
        scope = [node, *(self.top[item] for item in sorted(named) if item in self.top)]
        return any(_can_be(item, name) for part in scope for item in ast.walk(part))

    def helpers(self, node: ast.stmt, owner: str | None) -> list[Source]:
        names, attributes = _references(node)
        if isinstance(node, _FUNCTION):
            names |= {arg.arg for arg in node.args.args}
        found = [
            self.source(name, self.top[name])
            for name in sorted(names)
            if name in self.top and self.top[name] is not node
        ]
        if owner is not None:
            found += self._class_helpers(node, owner, attributes)
        return found

    def _class_helpers(self, node: ast.stmt, owner: str, attributes: set[str]) -> list[Source]:
        methods = self.classes[owner]
        wanted = sorted(attributes) + [hook for hook in _CLASS_HOOKS if hook not in attributes]
        return [
            self.source(f"{owner}.{name}", methods[name])
            for name in wanted
            if name in methods and methods[name] is not node
        ]


def _references(node: ast.AST) -> tuple[set[str], set[str]]:
    """The names ``node`` uses, and the ``self`` or ``cls`` attributes it reads."""
    names: set[str] = set()
    attributes: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif _is_own_attribute(item):
            attributes.add(item.attr)
    return names, attributes


def _is_own_attribute(item: ast.AST) -> TypeGuard[ast.Attribute]:
    return (
        isinstance(item, ast.Attribute)
        and isinstance(item.value, ast.Name)
        and item.value.id in ("self", "cls")
    )


def _at_import(node: ast.AST) -> Iterable[ast.AST]:
    """Yield the nodes of ``node`` that run when the module is imported (no function bodies)."""
    stack = [node]
    while stack:
        item = stack.pop()
        yield item
        if not isinstance(item, (*_FUNCTION, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(item))


def _constant_pieces(value: str) -> list[str | None]:
    """Return the pieces of a plain string, a hole between each ``format`` or ``%`` field."""
    pieces: list[str | None] = []
    for number, piece in enumerate(_HOLE.split(value)):
        pieces += [piece] if number == 0 else [None, piece]
    return pieces


def _template(node: ast.AST) -> list[str | None] | None:
    """Return the literal pieces of the string ``node`` makes, None for each hole.

    None when ``node`` makes no string with a literal piece: a plain constant
    (with any ``format`` or ``%`` holes), an f-string, or a ``+`` of them.
    """
    pieces: list[str | None] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        pieces = _constant_pieces(node.value)
    elif isinstance(node, ast.JoinedStr):
        pieces = [_literal(item) for item in node.values]
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        pieces = [*(_template(node.left) or [None]), *(_template(node.right) or [None])]
    return pieces if any(pieces) else None


def _literal(item: ast.AST) -> str | None:
    """The text of a string constant, None for any other f-string part."""
    if isinstance(item, ast.Constant) and isinstance(item.value, str):
        return item.value
    return None


def _can_be(node: ast.AST, name: str) -> bool:
    """Whether the string ``node`` makes can be ``name`` (or, as code, define it)."""
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", node.value)
    ):
        return True
    pieces = _template(node)
    if pieces is None:
        return False
    pattern = "".join(".+" if piece is None else re.escape(piece) for piece in pieces)
    return re.fullmatch(pattern, name) is not None


def split_node_id(node_id: str) -> tuple[str, list[str]]:
    """Return a node id's file and its name parts, parameters removed."""
    path, *parts = node_id.strip().split("::")
    return path, [part.split("[", 1)[0] for part in parts]


def extract(module_text: str, node_id: str, at_import: bool = False) -> TestSource | None:
    """Return the test a node id names in ``module_text``; None when absent.

    A test no ``def`` names may be made when the module is imported: it is
    returned as the statement that makes it, a module-level loop writing
    ``globals()`` (``at_import`` false) or any module-level statement that
    uses ``globals``, ``setattr``, ``exec``... (``at_import`` true).

    Raises SyntaxError when the module does not parse.
    """
    path, parts = split_node_id(node_id)
    module = _Module(path, module_text)
    if not parts:
        return None
    node: ast.stmt | None = None
    owner: str | None = None
    if len(parts) == 1:
        node = module.top.get(parts[0])
    elif len(parts) == METHOD_PARTS and parts[0] in module.classes:
        owner = parts[0]
        node = module.classes[owner].get(parts[1])
    if node is not None and isinstance(node, _FUNCTION):
        name = "::".join(parts)
        return TestSource(module.source(name, node), tuple(module.helpers(node, owner)))
    loop = module.generator(parts[-1], at_import)
    if loop is None:
        return None
    label = (
        "generated when the module is imported" if at_import else "generated by a module-level loop"
    )
    return TestSource(
        module.source(f"({label})", loop),
        tuple(module.helpers(loop, None)),
        generated=True,
    )


def imported_modules(module_text: str) -> set[str]:
    """Return the top-level names of the modules ``module_text`` imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(module_text)):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def numbered_lines(text: str) -> dict[int, str]:
    """Return the lines ``Source.numbered`` text shows, by line number."""
    shown: dict[int, str] = {}
    for line in source_lines(text):
        match = _NUMBERED.match(line)
        if match:
            shown[int(match.group(1))] = match.group(2)
    return shown
