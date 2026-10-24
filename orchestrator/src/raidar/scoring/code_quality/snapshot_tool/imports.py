"""Import extraction and entrypoint source tracing (runs in the tool env).

Port of ``slop-code-bench`` ``src/slop_code/metrics/languages/python/imports.py``
at ``25069ba``: tree-sitter import statements, local module resolution
(files, packages, relative levels, submodule names, executed package
initialisers) and the reachable-file walk from the entrypoint that bounds the
call graph (H). Logging is dropped; behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .parser import get_python_parser

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass
class ImportInfo:
    module_path: str | None
    is_relative: bool
    relative_level: int
    imported_names: list[str] = field(default_factory=list)
    line: int = 0


def _decode_node_text(node: Node) -> str:
    text = node.text
    if text is None:
        raise ValueError("Tree-sitter node text is unavailable")
    return text.decode("utf-8")


def _parse_import_statement(node: Node) -> list[ImportInfo]:
    """Parse every module in an ``import`` statement."""
    imports: list[ImportInfo] = []
    for child in node.children:
        module_node = child
        if child.type == "aliased_import":
            module_node = next((sub for sub in child.children if sub.type == "dotted_name"), child)
        if module_node.type == "dotted_name":
            imports.append(
                ImportInfo(
                    module_path=_decode_node_text(module_node),
                    is_relative=False,
                    relative_level=0,
                    line=node.start_point[0] + 1,
                )
            )
    return imports


def _parse_relative_import(node: Node) -> tuple[int, str | None]:
    level = 0
    module_path = None
    for child in node.children:
        if child.type == "import_prefix":
            level = len([c for c in child.children if c.type == "."])
        elif child.type == "dotted_name":
            module_path = _decode_node_text(child)
    return level, module_path


def _aliased_name(child: Node) -> str | None:
    for subchild in child.children:
        if subchild.type == "dotted_name":
            return _decode_node_text(subchild)
    return None


def _parse_import_from_statement(node: Node) -> ImportInfo:
    """Parse 'from X import Y' style statements including relative imports."""
    info = ImportInfo(module_path=None, is_relative=False, relative_level=0)
    found_import_keyword = False
    for child in node.children:
        kind = child.type
        if kind == "import":
            found_import_keyword = True
        elif kind == "relative_import":
            info.is_relative = True
            info.relative_level, info.module_path = _parse_relative_import(child)
        elif kind == "dotted_name" and not found_import_keyword:
            info.module_path = _decode_node_text(child)
        elif kind == "dotted_name":
            info.imported_names.append(_decode_node_text(child))
        elif kind == "aliased_import":
            name = _aliased_name(child)
            if name is not None:
                info.imported_names.append(name)
        elif kind == "wildcard_import":
            info.imported_names.append("*")
    info.line = node.start_point[0] + 1
    return info


def _extract_imports_from_node(node: Node, imports: list[ImportInfo]) -> None:
    for child in node.children:
        if child.type == "import_statement":
            imports.extend(_parse_import_statement(child))
        elif child.type == "import_from_statement":
            imports.append(_parse_import_from_statement(child))
        else:
            _extract_imports_from_node(child, imports)


def extract_imports(source: Path) -> list[ImportInfo]:
    """Every import statement in a Python source file."""
    code = source.read_text(encoding="utf-8")
    if not code.strip():
        return []
    tree = get_python_parser().parse(code.encode("utf-8"))
    imports: list[ImportInfo] = []
    _extract_imports_from_node(tree.root_node, imports)
    return imports


def _try_resolve_module(base_dir: Path, module_path: str, snapshot_dir: Path) -> Path | None:
    """A dotted module path resolved to a file or package initialiser, if local."""
    parts = module_path.split(".")
    target_dir = base_dir
    for part in parts[:-1]:
        target_dir = target_dir / part
        if not target_dir.exists():
            return None
    last_part = parts[-1]
    file_path = target_dir / f"{last_part}.py"
    if file_path.exists() and file_path.is_relative_to(snapshot_dir):
        return file_path
    package_path = target_dir / last_part / "__init__.py"
    if package_path.exists() and package_path.is_relative_to(snapshot_dir):
        return package_path
    return None


def _submodules(
    target_dir: Path, names: list[str], snapshot_dir: Path, results: list[Path]
) -> None:
    for name in names:
        if name == "*":
            continue
        resolved = _try_resolve_module(target_dir, name, snapshot_dir)
        if resolved:
            results.append(resolved)


def _resolve_relative_import(
    import_info: ImportInfo, importing_file: Path, snapshot_dir: Path
) -> list[Path]:
    results: list[Path] = []
    base_dir = importing_file.parent
    for _ in range(import_info.relative_level - 1):
        base_dir = base_dir.parent
    if import_info.module_path:
        resolved = _try_resolve_module(base_dir, import_info.module_path, snapshot_dir)
        if resolved:
            results.append(resolved)
    target_dir = base_dir
    if import_info.module_path:
        for part in import_info.module_path.split("."):
            target_dir = target_dir / part
    _submodules(target_dir, import_info.imported_names, snapshot_dir, results)
    return results


def _resolve_absolute_import(import_info: ImportInfo, snapshot_dir: Path) -> list[Path]:
    results: list[Path] = []
    if not import_info.module_path:
        return results
    resolved = _try_resolve_module(snapshot_dir, import_info.module_path, snapshot_dir)
    if resolved:
        results.append(resolved)
    if import_info.imported_names:
        module_dir = snapshot_dir
        for part in import_info.module_path.split("."):
            module_dir = module_dir / part
        _submodules(module_dir, import_info.imported_names, snapshot_dir, results)
    return results


def _package_initializers(resolved: Path, snapshot_dir: Path) -> list[Path]:
    """Regular-package initialisers executed before ``resolved``."""
    initializers: list[Path] = []
    directory = resolved.parent if resolved.name != "__init__.py" else resolved.parent.parent
    while directory.is_relative_to(snapshot_dir):
        initializer = directory / "__init__.py"
        if initializer.exists():
            initializers.append(initializer)
        if directory == snapshot_dir:
            break
        directory = directory.parent
    return initializers


def _resolve_import(
    import_info: ImportInfo, importing_file: Path, snapshot_dir: Path
) -> list[Path]:
    if import_info.is_relative:
        resolved = _resolve_relative_import(import_info, importing_file, snapshot_dir)
    else:
        resolved = _resolve_absolute_import(import_info, snapshot_dir)
    paths: list[Path] = []
    for path in resolved:
        paths.extend(_package_initializers(path, snapshot_dir))
        paths.append(path)
    return list(dict.fromkeys(paths))


def _local_imports(current: Path, snapshot_dir: Path) -> list[Path]:
    if not current.exists() or not current.is_file() or current.suffix != ".py":
        return []
    try:
        imports = extract_imports(current)
    except Exception:  # the fork logs and skips a file it cannot read or parse
        return []
    return [path for imp in imports for path in _resolve_import(imp, current, snapshot_dir)]


def trace_source_files(entrypoint: Path, snapshot_dir: Path) -> set[Path]:
    """Files (relative to ``snapshot_dir``) reachable from the entrypoint via local imports."""
    entrypoint_abs = (
        entrypoint if entrypoint.is_absolute() else (snapshot_dir / entrypoint).resolve()
    )
    if not entrypoint_abs.exists():
        return set()
    visited: set[Path] = set()
    to_visit: list[Path] = [entrypoint_abs]
    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)
        to_visit.extend(
            path for path in _local_imports(current, snapshot_dir) if path not in visited
        )
    return {path.relative_to(snapshot_dir) for path in visited if path.is_relative_to(snapshot_dir)}
