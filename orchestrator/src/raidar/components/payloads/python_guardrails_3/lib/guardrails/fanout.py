"""Architectural import fan-out (Enaible FANOUT.general, FANOUT.large-module).

Ported from check-python-fanout.py. Adaptations for a single-root workspace:
- discovery walks the filesystem with the gate's scope instead of ``git
  ls-files`` (the workspace need not be a git repository);
- the source root is the workspace root and the first-party root packages are
  the workspace's own top-level modules and packages, found at check time
  instead of a fixed ``--root-package`` list.
Counting, import normalisation, NLOC and the limits (10; 3 above 800 NLOC) are
Enaible's.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from guardrails.diagnostics import Diagnostic
from guardrails.scope import SourceFile

ARCHITECTURAL_MAX = 10
LARGE_MODULE_NLOC = 800
LARGE_MODULE_ARCHITECTURAL_MAX = 3


@dataclass(frozen=True)
class _Module:
    source: SourceFile
    name: str | None
    is_package: bool


def _module_name(relpath: str) -> tuple[str | None, bool]:
    path = PurePosixPath(relpath)
    if path.name == "__init__.py":
        parts = path.parts[:-1]
        return (".".join(parts) if parts else None), True
    return ".".join((*path.parts[:-1], path.stem)), False


def _resolve_relative(current: _Module, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current.name is None:
        return None
    parts = current.name.split(".") if current.is_package else current.name.split(".")[:-1]
    if node.level > 1:
        parts = parts[: -(node.level - 1)]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _normalise(module: str, roots: set[str], known: set[str]) -> str:
    if module.split(".", 1)[0] not in roots:
        return module
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return parts[0]


def _literal_import_module(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return None
    name = node.args[0].value
    return None if name.startswith(".") else name


def _from_imports(
    node: ast.ImportFrom, current: _Module, roots: set[str], known: set[str]
) -> set[str]:
    module = _resolve_relative(current, node)
    if module is None:
        return set()
    if module.split(".", 1)[0] not in roots:
        return {module}
    found: set[str] = set()
    for alias in node.names:
        candidate = module if alias.name == "*" else f"{module}.{alias.name}"
        target = candidate if candidate in known else module
        found.add(_normalise(target, roots, known))
    return found


def _node_imports(node: ast.AST, current: _Module, roots: set[str], known: set[str]) -> set[str]:
    dynamic = _literal_import_module(node)
    if dynamic is not None:
        return {_normalise(dynamic, roots, known)}
    if isinstance(node, ast.Import):
        return {_normalise(alias.name, roots, known) for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return _from_imports(node, current, roots, known)
    return set()


def _imports(tree: ast.AST, current: _Module, roots: set[str], known: set[str]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        found |= _node_imports(node, current, roots, known)
    if current.name is not None:
        found.discard(current.name)
    return found


def _is_stdlib(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def _nloc(text: str) -> int:
    return sum(
        1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def _known_modules(modules: Sequence[_Module]) -> set[str]:
    known = {module.name for module in modules if module.name}
    for name in tuple(known):
        parts = name.split(".")
        known.update(".".join(parts[:end]) for end in range(1, len(parts)))
    return known


def _module_diagnostics(relpath: str, nloc: int, dependencies: list[str]) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    listed = ", ".join(dependencies)
    if len(dependencies) > ARCHITECTURAL_MAX:
        message = f"architectural imports {len(dependencies)} > {ARCHITECTURAL_MAX} ({listed})"
        found.append(Diagnostic("FANOUT.general", relpath, 1, message))
    if nloc > LARGE_MODULE_NLOC and len(dependencies) > LARGE_MODULE_ARCHITECTURAL_MAX:
        message = (
            f"large module {nloc} NLOC with architectural imports "
            f"{len(dependencies)} > {LARGE_MODULE_ARCHITECTURAL_MAX} ({listed})"
        )
        found.append(Diagnostic("FANOUT.large-module", relpath, 1, message))
    return found


def fanout_diagnostics(sources: Sequence[SourceFile]) -> list[Diagnostic]:
    """Return one diagnostic per module over either fan-out limit."""
    modules = [_Module(source, *_module_name(source.relpath)) for source in sources]
    known = _known_modules(modules)
    roots = {name.split(".", 1)[0] for name in known}
    found: list[Diagnostic] = []
    for module in modules:
        try:
            text = module.source.path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=module.source.relpath)
        except (UnicodeDecodeError, SyntaxError):
            continue  # PYQ299 / PYS000 already fail this file closed
        dependencies = sorted(
            dep for dep in _imports(tree, module, roots, known) if not _is_stdlib(dep)
        )
        found += _module_diagnostics(module.source.relpath, _nloc(text), dependencies)
    return found
