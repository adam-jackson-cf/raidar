"""A mapped test's source with one hop of helpers, from the retained final test files.

The files kept under the build's ``sources`` directory are the final test modules and the helper
modules they import (no production code). A test id is ``path::name`` or ``path::Class::name``
(a ``[param]`` suffix is folded). Helpers are the top-level functions and classes the test names,
in its module or in an imported kept module, and the fixtures named by its parameters in its
module or the ``conftest.py`` files above it; helpers of helpers are not followed. The source is
what Jev reads; ``has_assertion`` and ``skipped`` are facts read from the syntax tree (an
``assert`` of anything but a constant, ``pytest.raises`` or a ``self.assert*`` call; a skip or
xfail marker, call or module ``pytestmark``) kept beside Jev's answer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SKIPS = frozenset({"skip", "skipif", "xfail", "skipTest"})
_Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class ReadTest:
    id: str
    source: str
    helpers: str
    has_assertion: bool
    skipped: bool


@dataclass(frozen=True)
class _Module:
    path: Path
    text: str
    tree: ast.Module

    def defs(self) -> dict[str, _Def]:
        return {node.name: node for node in self.tree.body if isinstance(node, _Def)}

    def segment(self, node: _Def) -> str:
        first = min([node.lineno, *(item.lineno for item in node.decorator_list)])
        return "\n".join(self.text.splitlines()[first - 1 : node.end_lineno])


def _load(path: Path) -> _Module | None:
    try:
        text = path.read_text(encoding="utf-8")
        return _Module(path, text, ast.parse(text))
    except (OSError, SyntaxError, ValueError):
        return None


def _inside(root: Path, relative: str) -> Path | None:
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) and path.is_file() else None


def _find(module: _Module, names: list[str]) -> _Def | None:
    scope: list[ast.stmt] = module.tree.body
    node: _Def | None = None
    for name in names:
        node = next((item for item in scope if isinstance(item, _Def) and item.name == name), None)
        if node is None:
            return None
        scope = node.body
    return node


def _names(node: ast.AST) -> set[str]:
    found = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        found |= {arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)}
    return found


def _marker(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
    return name in _SKIPS


def _asserts(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Assert) and not isinstance(item.test, ast.Constant):
            return True
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
            name = item.func.attr
            if name == "raises" or (name.startswith("assert") and name != "assert_"):
                return True
    return False


def _marked(scope: _Def | None) -> bool:
    return scope is not None and any(_marker(mark) for mark in scope.decorator_list)


def _module_marked(module: _Module) -> bool:
    return any(
        isinstance(item, ast.Assign)
        and any(getattr(target, "id", "") == "pytestmark" for target in item.targets)
        and any(_marker(mark) for mark in ast.walk(item.value))
        for item in module.tree.body
    )


def _skipped(module: _Module, node: _Def, owner: _Def | None) -> bool:
    calls = (item for item in ast.walk(node) if isinstance(item, ast.Call))
    return (
        _marked(node)
        or _marked(owner)
        or _module_marked(module)
        or any(_marker(item) for item in calls)
    )


def _import_path(root: Path, item: ast.stmt) -> Path | None:
    if not isinstance(item, ast.ImportFrom) or item.module is None or item.level:
        return None
    base = item.module.replace(".", "/")
    return _inside(root, f"{base}.py") or _inside(root, f"{base}/__init__.py")


def _imported(root: Path, module: _Module, wanted: set[str]) -> list[tuple[_Module, _Def]]:
    found: list[tuple[_Module, _Def]] = []
    for item in module.tree.body:
        path = _import_path(root, item)
        other = _load(path) if path else None
        if other is None or not isinstance(item, ast.ImportFrom):
            continue
        defs = other.defs()
        found.extend(
            (other, defs[alias.name])
            for alias in item.names
            if alias.name in wanted and alias.name in defs
        )
    return found


def _conftests(root: Path, module: _Module) -> list[_Module]:
    found = []
    directory = module.path.parent
    top = root.resolve()
    while directory.is_relative_to(top):
        loaded = _load(directory / "conftest.py")
        if loaded is not None:
            found.append(loaded)
        directory = directory.parent
    return found


def _target(root: Path, test_id: str) -> tuple[_Module, _Def, _Def | None] | None:
    """The module, test node and owning class of a test id, if the kept files hold them."""
    path_part, *names = test_id.split("::")
    if not names:
        return None
    names[-1] = names[-1].split("[", 1)[0]
    path = _inside(root, path_part)
    module = _load(path) if path else None
    node = _find(module, names) if module else None
    if module is None or node is None:
        return None
    return module, node, _find(module, names[:-1]) if len(names) > 1 else None


def _helpers(
    root: Path, module: _Module, node: _Def, owner: _Def | None
) -> list[tuple[_Module, _Def]]:
    """One hop of helpers, each once: same module, imported kept modules, conftests."""
    wanted = _names(node) - {node.name}
    local = module.defs()
    helpers: list[tuple[_Module, _Def]] = [
        (module, local[name])
        for name in sorted(wanted)
        if name in local and local[name] is not owner
    ]
    helpers += _imported(root, module, wanted)
    for conftest in _conftests(root, module):
        defs = conftest.defs()
        helpers += [(conftest, defs[name]) for name in sorted(wanted) if name in defs]
    unique: dict[tuple[Path, int], tuple[_Module, _Def]] = {}
    for holder, item in helpers:
        unique.setdefault((holder.path, item.lineno), (holder, item))
    return list(unique.values())


def read_test(root: Path, test_id: str) -> ReadTest | None:
    """The test's source and helpers, or ``None`` when the retained files do not hold it."""
    target = _target(root, test_id)
    if target is None:
        return None
    module, node, owner = target
    unique = _helpers(root, module, node, owner)
    text = "\n\n".join(
        f"# {holder.path.resolve().relative_to(root.resolve()).as_posix()}\n{holder.segment(item)}"
        for holder, item in unique
    )
    return ReadTest(
        id=test_id,
        source=module.segment(node),
        helpers=text,
        has_assertion=_asserts(node) or any(_asserts(item) for _, item in unique),
        skipped=_skipped(module, node, owner),
    )
