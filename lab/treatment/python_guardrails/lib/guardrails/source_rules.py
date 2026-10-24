"""Per-file AST rules (Enaible PYQ200/202/203/299, PYS000/250) plus two workspace rules.

Ported from check-python-quality.py and check-python-scalability.py with the
Enaible-only boundaries (analyzer bases, seam wrappers, adapter imports,
contracts.py, analyzer I/O and ownership) left out: a general workspace has no
such subsystem. Thresholds are Enaible's.

Workspace rules:
- ``SUPPRESS``: a lint or type suppression comment (``# noqa``, ``# ruff: noqa``,
  ``# type: ignore``, ``# mypy: ...``). This is the "detect added suppressions"
  half of the RUFF.IGNORES contract: the workspace starts empty, so every
  suppression is an added one, and the policy's own ignores are the only ones.
- ``TST001``: a ``subprocess`` call in a test file without ``timeout=``
  (TEST.subprocess-timeouts).
"""

from __future__ import annotations

import ast
import io
import re
import tokenize

from guardrails.diagnostics import Diagnostic
from guardrails.scope import SourceFile

CLASS_MAX_LINES = 700
CLASS_MAX_INIT_ARGS = 8
CLASS_MAX_INSTANCE_ATTRIBUTES = 12
MAX_LOOP_DEPTH = 2
SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output", "Popen"})
_SUPPRESSION = re.compile(r"#\s*(noqa\b|ruff\s*:\s*noqa|type\s*:\s*ignore|mypy\s*:)", re.IGNORECASE)


def _self_attributes(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = list(node.body)
    while pending:
        current = pending.pop()
        if isinstance(current, ast.ClassDef):
            continue
        if (
            isinstance(current, ast.Attribute)
            and isinstance(current.ctx, ast.Store)
            and isinstance(current.value, ast.Name)
            and current.value.id == "self"
        ):
            names.add(current.attr)
        pending.extend(ast.iter_child_nodes(current))
    return names


def _init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = [*method.args.posonlyargs, *method.args.args]
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def _class_shape(node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    lines = (node.end_lineno or node.lineno) - node.lineno + 1
    if lines > CLASS_MAX_LINES:
        found.append(
            Diagnostic(
                "PYQ200",
                relpath,
                node.lineno,
                f"class '{node.name}' has {lines} lines > {CLASS_MAX_LINES}",
            )
        )
    attributes = _self_attributes(node)
    if len(attributes) > CLASS_MAX_INSTANCE_ATTRIBUTES:
        found.append(
            Diagnostic(
                "PYQ203",
                relpath,
                node.lineno,
                f"class '{node.name}' assigns {len(attributes)} instance attributes"
                f" > {CLASS_MAX_INSTANCE_ATTRIBUTES}",
            )
        )
    for method in node.body:
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef) and method.name == "__init__":
            count = _init_arg_count(method)
            if count > CLASS_MAX_INIT_ARGS:
                found.append(
                    Diagnostic(
                        "PYQ202",
                        relpath,
                        method.lineno,
                        f"constructor for '{node.name}' has {count} parameters"
                        f" > {CLASS_MAX_INIT_ARGS}",
                    )
                )
    return found


class LoopDepthVisitor(ast.NodeVisitor):
    """Enaible's PYS250 loop and comprehension depth walk, analyzer checks removed."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.diagnostics: list[Diagnostic] = []
        self._functions: list[str] = []
        self._depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self._visit_loop(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous, self._depth = self._depth, 0
        self.visit(node.body)
        self._depth = previous

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self._functions.append(node.name)
        previous, self._depth = self._depth, 0
        for statement in node.body:
            self.visit(statement)
        self._depth = previous
        self._functions.pop()

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if isinstance(node, ast.For | ast.AsyncFor):
            self.visit(node.iter)
        self._enter(node.lineno)
        if isinstance(node, ast.While):
            self.visit(node.test)
        else:
            self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        self._depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _visit_comprehension(
        self, generators: list[ast.comprehension], results: tuple[ast.expr, ...], index: int = 0
    ) -> None:
        if index == len(generators):
            for result in results:
                self.visit(result)
            return
        generator = generators[index]
        self.visit(generator.iter)
        self._enter(generator.target.lineno)
        self.visit(generator.target)
        for condition in generator.ifs:
            self.visit(condition)
        self._visit_comprehension(generators, results, index + 1)
        self._depth -= 1

    def _enter(self, lineno: int) -> None:
        self._depth += 1
        if self._depth > MAX_LOOP_DEPTH:
            owner = self._functions[-1] if self._functions else "<module>"
            self.diagnostics.append(
                Diagnostic(
                    "PYS250",
                    self.relpath,
                    lineno,
                    f"nested loop depth {self._depth} exceeds {MAX_LOOP_DEPTH} in {owner}",
                )
            )


def _untimed_subprocess_call(node: ast.AST) -> str | None:
    """The ``subprocess`` function a call uses without ``timeout=``, else None."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SUBPROCESS_CALLS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and not any(keyword.arg == "timeout" for keyword in node.keywords)
    ):
        return node.func.attr
    return None


def _subprocess_without_timeout(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    return [
        Diagnostic("TST001", relpath, node.lineno, f"subprocess.{name} in a test needs timeout=")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _untimed_subprocess_call(node)) is not None
    ]


def _suppressions(text: str, relpath: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return found
    message = "lint/type suppression comments are not allowed; fix the finding instead"
    found.extend(
        Diagnostic("SUPPRESS", relpath, token.start[0], message, col=token.start[1] + 1)
        for token in tokens
        if token.type == tokenize.COMMENT and _SUPPRESSION.search(token.string)
    )
    return found


def source_diagnostics(source: SourceFile) -> list[Diagnostic]:
    """Return every per-file AST and comment diagnostic; unreadable source fails closed."""
    try:
        text = source.path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [
            Diagnostic("PYQ299", source.relpath, 1, f"could not decode Python source: {exc.reason}")
        ]
    try:
        tree = ast.parse(text, filename=source.relpath)
    except SyntaxError as exc:
        return [
            Diagnostic(
                "PYS000", source.relpath, exc.lineno or 1, f"could not parse Python file: {exc.msg}"
            )
        ]
    found: list[Diagnostic] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            found.extend(_class_shape(node, source.relpath))
    visitor = LoopDepthVisitor(source.relpath)
    visitor.visit(tree)
    found.extend(visitor.diagnostics)
    found.extend(_suppressions(text, source.relpath))
    if source.is_test:
        found.extend(_subprocess_without_timeout(tree, source.relpath))
    return found
