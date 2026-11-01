"""Static local import closures, including nested imports and package initializers.

Shared by primitive and grading identities. Modules are inspected, never imported.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import raidar_contract

RAIDAR_DIR = Path(__file__).resolve().parents[1]
CONTRACT_DIR = Path(raidar_contract.__file__).resolve().parent
ROOT_PACKAGE = "raidar"
CONTRACT_PACKAGE = "raidar_contract"


def _module_base(module: str) -> Path | None:
    root, _, rest = module.partition(".")
    if root == ROOT_PACKAGE:
        return RAIDAR_DIR.parent / module.replace(".", "/")
    if root == CONTRACT_PACKAGE:
        return CONTRACT_DIR / rest.replace(".", "/") if rest else CONTRACT_DIR
    return None


def is_namespace(module: str) -> bool:
    """Whether module is a local implicit namespace with no executable initializer."""
    base = _module_base(module)
    return base is not None and base.is_dir() and not (base / "__init__.py").is_file()


def source_path(module: str) -> Path | None:
    """The source of a local module, or None for namespaces/third-party/unknown modules."""
    base = _module_base(module)
    if base is None:
        return None
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    init = base / "__init__.py"
    return init if init.is_file() else None


def _from_base(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return node.module or ""
    anchor = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
    return f"{anchor}.{node.module}" if node.module else anchor


def _statement_names(node: ast.AST, package: str) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        base = _from_base(node, package)
        return {base, *(f"{base}.{alias.name}" for alias in node.names)}
    return set()


@lru_cache(maxsize=256)
def _imported(module: str, path: Path, _stat_key: tuple[int, int, int]) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        names |= _statement_names(node, package)
    return {name for name in names if name.split(".")[0] in (ROOT_PACKAGE, CONTRACT_PACKAGE)}


def import_closure(modules: str | tuple[str, ...]) -> frozenset[str]:
    """Every local module/initializer reached by the roots, following current source imports."""
    found: set[str] = set()
    pending = [modules] if isinstance(modules, str) else list(modules)
    while pending:
        name = pending.pop()
        if name in found:
            continue
        path = source_path(name)
        if path is None and not is_namespace(name):
            continue
        found.add(name)
        parent = name.rpartition(".")[0]
        if parent:
            pending.append(parent)
        if path is not None:
            facts = path.stat()
            pending.extend(
                _imported(name, path, (facts.st_mtime_ns, facts.st_ctime_ns, facts.st_size))
            )
    return frozenset(found)
