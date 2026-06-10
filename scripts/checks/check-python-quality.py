#!/usr/bin/env python3
"""Validate maintained Python structure and class shape."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CLASS_MAX_LINES = 700
CLASS_MAX_PUBLIC_METHODS = 20
CLASS_MAX_INIT_ARGS = 8
CLASS_MAX_INSTANCE_ATTRIBUTES = 12

MAINTAINED_ROOTS = (
    "orchestrator/src/raidar/",
    "orchestrator/tests/",
    "scripts/checks/",
    "scripts/release/",
)
EXCLUDED_PREFIXES = (
    ".cache/",
    ".git/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tmp/",
    ".venv/",
    "docs/",
    "experiments/",
    "orchestrator/.cache/",
    "orchestrator/.venv/",
    "scenarios/",
)
GENERIC_NAME_PARTS = {"helpers", "misc", "stuff", "temp"}
ALLOWED_TEST_ROOT_FILES = {
    "orchestrator/tests/__init__.py",
    "orchestrator/tests/conftest.py",
}
SCRIPT_NAME_ALLOWLIST = {
    "bump-version.py",
    "check-python-fanout.py",
    "check-python-quality.py",
    "verify-version-wiring.py",
}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code} {self.message}"


def repo_relative_path(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def path_matches_prefix(relpath: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    return relpath == normalized_prefix or relpath.startswith(f"{normalized_prefix}/")


def is_excluded_relpath(relpath: str) -> bool:
    normalized = relpath.lstrip("./")
    return normalized.startswith(EXCLUDED_PREFIXES) or "/__pycache__/" in f"/{normalized}/"


def is_maintained_python_relpath(relpath: str) -> bool:
    normalized = relpath.lstrip("./")
    if not normalized.endswith(".py") or is_excluded_relpath(normalized):
        return False
    return any(path_matches_prefix(normalized, root) for root in MAINTAINED_ROOTS)


def discover_maintained_python_files(repo_root: Path) -> list[Path]:
    return sorted(
        path
        for path in repo_root.rglob("*.py")
        if is_maintained_python_relpath(repo_relative_path(repo_root, path))
    )


def _is_snake_case_name(name: str) -> bool:
    if name == "__init__":
        return True
    if not name or name[0].isdigit():
        return False
    return all(char == "_" or char.islower() or char.isdigit() for char in name)


def _root_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in MAINTAINED_ROOTS):
        return []
    return [
        Diagnostic(
            "PYQ100",
            relpath,
            1,
            "Python file is outside canonical maintained roots",
        )
    ]


def _generic_name_diagnostics(relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parts = Path(relpath).parts[:-1]
    for part in parts:
        if part in GENERIC_NAME_PARTS:
            diagnostics.append(
                Diagnostic(
                    "PYQ103",
                    relpath,
                    1,
                    f"generic catch-all directory name '{part}' is not allowed",
                )
            )
    stem = Path(relpath).stem
    if stem in GENERIC_NAME_PARTS:
        diagnostics.append(
            Diagnostic(
                "PYQ104",
                relpath,
                1,
                f"generic catch-all module name '{stem}' is not allowed",
            )
        )
    return diagnostics


def _module_name_diagnostics(relpath: str) -> list[Diagnostic]:
    if relpath.startswith("scripts/") and Path(relpath).name in SCRIPT_NAME_ALLOWLIST:
        return []
    stem = Path(relpath).stem
    if stem.startswith("test_") or _is_snake_case_name(stem):
        return []
    return [
        Diagnostic(
            "PYQ107",
            relpath,
            1,
            f"Python module '{Path(relpath).name}' must use snake_case",
        )
    ]


def _package_dir_diagnostics(relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    ignored_parts = {
        "checks",
        "orchestrator",
        "raidar",
        "release",
        "scripts",
        "src",
        "tests",
    }
    for part in Path(relpath).parts[:-1]:
        if part in ignored_parts or _is_snake_case_name(part):
            continue
        diagnostics.append(
            Diagnostic(
                "PYQ105",
                relpath,
                1,
                f"Python package directory '{part}' must use snake_case",
            )
        )
    return diagnostics


def structure_diagnostics_for_relpath(relpath: str) -> list[Diagnostic]:
    normalized = relpath.lstrip("./")
    if is_excluded_relpath(normalized):
        return []
    diagnostics = _root_diagnostics(normalized)
    if diagnostics:
        return diagnostics
    if normalized in ALLOWED_TEST_ROOT_FILES:
        return []
    diagnostics.extend(_package_dir_diagnostics(normalized))
    diagnostics.extend(_module_name_diagnostics(normalized))
    diagnostics.extend(_generic_name_diagnostics(normalized))
    return diagnostics


def structure_diagnostics(repo_root: Path, paths: Sequence[Path]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        diagnostics.extend(structure_diagnostics_for_relpath(repo_relative_path(repo_root, path)))
    return diagnostics


def _public_methods(class_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    ]


def _init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = list(method.args.posonlyargs) + list(method.args.args)
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def _self_attributes(class_node: ast.ClassDef) -> set[str]:
    attributes: set[str] = set()
    for node in ast.walk(class_node):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.ctx, ast.Store):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            attributes.add(node.attr)
    return attributes


def _class_shape_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    line_count = (
        (getattr(class_node, "end_lineno", class_node.lineno) or class_node.lineno)
        - class_node.lineno
        + 1
    )
    if line_count > CLASS_MAX_LINES:
        diagnostics.append(
            Diagnostic(
                "PYQ200",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {line_count} lines > {CLASS_MAX_LINES}",
            )
        )
    public_methods = _public_methods(class_node)
    if len(public_methods) > CLASS_MAX_PUBLIC_METHODS:
        diagnostics.append(
            Diagnostic(
                "PYQ201",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {len(public_methods)} public methods > {CLASS_MAX_PUBLIC_METHODS}",
            )
        )
    attributes = _self_attributes(class_node)
    if len(attributes) > CLASS_MAX_INSTANCE_ATTRIBUTES:
        diagnostics.append(
            Diagnostic(
                "PYQ203",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' assigns {len(attributes)} instance attributes > {CLASS_MAX_INSTANCE_ATTRIBUTES}",
            )
        )
    return diagnostics


def _constructor_diagnostics(class_node: ast.ClassDef, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for method in class_node.body:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if method.name != "__init__":
            continue
        arg_count = _init_arg_count(method)
        if arg_count > CLASS_MAX_INIT_ARGS:
            diagnostics.append(
                Diagnostic(
                    "PYQ202",
                    relpath,
                    method.lineno,
                    f"constructor for '{class_node.name}' has {arg_count} parameters > {CLASS_MAX_INIT_ARGS}",
                )
            )
    return diagnostics


def class_interface_diagnostics_for_source(source: str, relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    tree = ast.parse(source, filename=relpath)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        diagnostics.extend(_class_shape_diagnostics(node, relpath))
        diagnostics.extend(_constructor_diagnostics(node, relpath))
    return diagnostics


def class_interface_diagnostics(repo_root: Path, paths: Sequence[Path]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        relpath = repo_relative_path(repo_root, path)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            diagnostics.append(Diagnostic("PYQ299", relpath, 1, f"could not decode source: {exc}"))
            continue
        diagnostics.extend(class_interface_diagnostics_for_source(source, relpath))
    return diagnostics


def _selected_paths(repo_root: Path, raw_paths: Sequence[str]) -> list[Path]:
    if not raw_paths:
        return discover_maintained_python_files(repo_root)
    selected: list[Path] = []
    for raw in raw_paths:
        path = (repo_root / raw).resolve()
        if path.is_dir():
            selected.extend(
                child
                for child in path.rglob("*.py")
                if is_maintained_python_relpath(repo_relative_path(repo_root, child))
            )
            continue
        if is_maintained_python_relpath(repo_relative_path(repo_root, path)):
            selected.append(path)
    return sorted(set(selected))


def _filter_checks(checks: Sequence[str]) -> set[str]:
    requested = set(checks)
    if "all" in requested:
        return {"structure", "class-interface"}
    return requested


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check",
        action="append",
        choices=("all", "structure", "class-interface"),
        default=None,
    )
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    paths = _selected_paths(repo_root, args.paths)
    checks = _filter_checks(args.check or ["all"])
    diagnostics: list[Diagnostic] = []
    if "structure" in checks:
        diagnostics.extend(structure_diagnostics(repo_root, paths))
    if "class-interface" in checks:
        diagnostics.extend(class_interface_diagnostics(repo_root, paths))

    if diagnostics:
        print("[python-quality] violations:", file=sys.stderr)
        for diagnostic in sorted(diagnostics, key=lambda item: item.render()):
            print(diagnostic.render(), file=sys.stderr)
        return 1
    print(f"[python-quality] OK: {len(paths)} maintained Python files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
