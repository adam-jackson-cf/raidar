"""Complete grading import closure and explicit name-loaded dependencies."""

from __future__ import annotations

import ast
import hashlib
import platform
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from raidar.contract import source_imports
from raidar.contract.source_digest import SOURCE_DIGEST_SCHEME, python_source_digest
from raidar.grading.payload import DYNAMIC_IMPORTS, PLUGIN_ROOTS, SUBPROCESS_ROOTS, runtime_files

GRADING_DIR = Path(__file__).resolve().parent
_DYNAMIC_LOADERS = frozenset(
    {"__import__", "import_module", "spec_from_file_location", "entry_points"}
)


def grading_roots() -> tuple[str, ...]:
    """Every grading module is an entry, including subprocess/plugin entry points."""
    modules = []
    for path in sorted(GRADING_DIR.rglob("*.py")):
        parts = path.relative_to(source_imports.RAIDAR_DIR.parent).with_suffix("").parts
        if "__pycache__" not in parts:
            modules.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return tuple(sorted(set(modules) | set(SUBPROCESS_ROOTS)))


def _loader_names(tree: ast.AST) -> set[str]:
    names = set(_DYNAMIC_LOADERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name for alias in node.names if alias.name in _DYNAMIC_LOADERS
            )
    return names


def _dynamic_targets(tree: ast.AST) -> set[str | None]:
    loaders = _loader_names(tree)
    targets: set[str | None] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
        if name in loaders:
            first = node.args[0] if node.args else None
            targets.add(
                first.value
                if isinstance(first, ast.Constant) and isinstance(first.value, str)
                else None
            )
    return targets


def _literal_constants(tree: ast.AST) -> dict[str, object]:
    return {
        target.id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _command_module(value: ast.expr, constants: dict[str, object]) -> str | None:
    name: object
    if isinstance(value, ast.Constant):
        name = value.value
    elif isinstance(value, ast.Name):
        name = constants.get(value.id)
    else:
        return None
    return name if isinstance(name, str) else None


def _command_roots(tree: ast.AST) -> set[str | None]:
    roots: set[str | None] = set()
    constants = _literal_constants(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple | ast.List):
            continue
        for flag, value in zip(node.elts, node.elts[1:], strict=False):
            if not isinstance(flag, ast.Constant) or flag.value not in {"-m", "-p"}:
                continue
            roots.add(_command_module(value, constants))
    return roots


@lru_cache(maxsize=256)
def _check_dynamic(path: Path, module: str, _stat_key: tuple[int, int, int]) -> None:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    unknown = _dynamic_targets(tree) - DYNAMIC_IMPORTS.get(module, frozenset())
    allowed_commands = {*PLUGIN_ROOTS, "raidar_oracle_plugin", "no:cacheprovider"}
    unknown |= _command_roots(tree) - allowed_commands
    if unknown:
        raise ValueError(
            f"unaccounted grading dynamic roots in {module}: {sorted(unknown, key=str)}"
        )


def _facts(path: Path) -> tuple[int, int, int]:
    facts = path.stat()
    return facts.st_mtime_ns, facts.st_ctime_ns, facts.st_size


@lru_cache(maxsize=1)
def _source_inventory(
    roots: tuple[str, ...],
) -> tuple[dict[str, Path], dict[Path, tuple[int, int, int]]]:
    modules = source_imports.import_closure(roots)
    watched: set[Path] = set()
    sources = {}
    for module in sorted(modules):
        path = source_imports.source_path(module)
        if path is None:
            if source_imports.is_namespace(module):
                package_root = (
                    source_imports.CONTRACT_DIR.parent
                    if module.startswith("raidar_contract")
                    else source_imports.RAIDAR_DIR.parent
                )
                watched.add(package_root / module.replace(".", "/"))
                continue
            raise ValueError(f"grading source unavailable: {module}")
        watched.update((path, path.parent))
        _check_dynamic(path, module, _facts(path))
        root = (
            source_imports.CONTRACT_DIR.parent
            if module.startswith("raidar_contract")
            else source_imports.RAIDAR_DIR
        )
        sources[path.relative_to(root).as_posix()] = path
    return sources, {path: _facts(path) for path in sorted(watched)}


def _changed(watched: dict[Path, tuple[int, int, int]]) -> bool:
    try:
        return any(_facts(path) != facts for path, facts in watched.items())
    except FileNotFoundError:
        return True


def grading_sources() -> dict[str, Path]:
    """Complete closure, refreshed when source bytes or source-directory entries change."""
    roots = grading_roots()
    sources, watched = _source_inventory(roots)
    if _changed(watched):
        _source_inventory.cache_clear()
        sources, _ = _source_inventory(roots)
    return dict(sources)


@lru_cache(maxsize=256)
def _source_digest(path: Path, _stat_key: tuple[int, int, int]) -> str:
    return python_source_digest(path)


@lru_cache(maxsize=1)
def _host_runtime() -> tuple[str, str, str]:
    return platform.python_version(), version("packaging"), version("pydantic")


def grading_execution_inventory() -> dict[str, object]:
    """The exact canonical source, shipped-resource and host-runtime identity inputs."""
    return {
        "scheme": SOURCE_DIGEST_SCHEME,
        "sources": {
            name: _source_digest(path, _facts(path)) for name, path in grading_sources().items()
        },
        "runtime": {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in runtime_files().items()
        },
        "host_runtime": _host_runtime(),
    }
