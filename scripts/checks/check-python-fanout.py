#!/usr/bin/env python3
"""Fail when Python modules have broad architectural fan-out."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDED_PREFIXES = (
    "docs/",
    "experiments/",
    "scenarios/",
)


@dataclass(frozen=True)
class PythonFile:
    path: Path
    relpath: str
    module: str | None
    is_package: bool


@dataclass(frozen=True)
class FanoutResult:
    relpath: str
    count: int
    limit: int
    nloc: int
    dependencies: tuple[str, ...]
    reasons: tuple[str, ...]


def _run_git(repo_root: Path, args: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _gitignored_paths(repo_root: Path, relpaths: list[str]) -> set[str]:
    if not relpaths:
        return set()
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=repo_root,
        input="\n".join(relpaths),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise SystemExit(result.stderr.strip() or "git check-ignore failed")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _is_project_excluded(relpath: str) -> bool:
    return relpath.startswith(DEFAULT_EXCLUDED_PREFIXES)


def discover_python_files(repo_root: Path) -> list[Path]:
    output = _run_git(
        repo_root,
        [
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            ":(glob)**/*.py",
        ],
    )
    relpaths = sorted(path for path in output.split("\0") if path)
    ignored = _gitignored_paths(repo_root, relpaths)
    return [
        repo_root / relpath
        for relpath in relpaths
        if relpath not in ignored
        and not _is_project_excluded(relpath)
        and (repo_root / relpath).is_file()
    ]


def _module_for_path(repo_root: Path, path: Path) -> tuple[str | None, bool]:
    rel = path.relative_to(repo_root)
    parts = rel.parts
    if len(parts) >= 4 and parts[:3] == ("orchestrator", "src", "raidar"):
        package_parts = parts[2:-1]
        is_package = path.name == "__init__.py"
        module_parts = package_parts if is_package else (*package_parts, path.stem)
        return ".".join(module_parts), is_package
    return None, False


def collect_python_files(repo_root: Path) -> list[PythonFile]:
    files: list[PythonFile] = []
    for path in discover_python_files(repo_root):
        module, is_package = _module_for_path(repo_root, path)
        files.append(
            PythonFile(
                path=path,
                relpath=path.relative_to(repo_root).as_posix(),
                module=module,
                is_package=is_package,
            )
        )
    return files


def _resolve_relative_import(current: PythonFile, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current.module is None:
        return None
    package_parts = (
        current.module.split(".")
        if current.is_package
        else current.module.split(".")[:-1]
    )
    if node.level > 1:
        package_parts = package_parts[: -(node.level - 1)]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _normalise_known_module(
    module: str,
    *,
    root_package: str,
    known_modules: set[str],
) -> str:
    if module != root_package and not module.startswith(f"{root_package}."):
        return module
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known_modules:
            return candidate
    return root_package if module == root_package else module


def imported_modules(
    source: str,
    current: PythonFile,
    *,
    root_package: str,
    known_modules: set[str],
) -> set[str]:
    tree = ast.parse(source, filename=current.relpath)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependency = _normalise_known_module(
                    alias.name,
                    root_package=root_package,
                    known_modules=known_modules,
                )
                imports.add(dependency)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_relative_import(current, node)
            if module is None:
                continue
            if module != root_package and not module.startswith(f"{root_package}."):
                imports.add(module)
                continue
            for alias in node.names:
                candidate = module if alias.name == "*" else f"{module}.{alias.name}"
                dependency = _normalise_known_module(
                    candidate,
                    root_package=root_package,
                    known_modules=known_modules,
                )
                if candidate not in known_modules:
                    dependency = _normalise_known_module(
                        module,
                        root_package=root_package,
                        known_modules=known_modules,
                    )
                imports.add(dependency)
    if current.module is not None:
        imports.discard(current.module)
    return imports


def _is_stdlib_module(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def architectural_dependencies(imports: set[str]) -> set[str]:
    return {module for module in imports if not _is_stdlib_module(module)}


def source_nloc(source: str) -> int:
    return sum(
        1
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def analyse_fanout(
    repo_root: Path,
    *,
    root_package: str,
    architectural_max: int,
    large_module_nloc: int,
    large_module_architectural_max: int,
) -> list[FanoutResult]:
    files = collect_python_files(repo_root)
    known_modules = {python_file.module for python_file in files if python_file.module}
    known_modules.add(root_package)
    results: list[FanoutResult] = []
    for python_file in files:
        source = python_file.path.read_text(encoding="utf-8")
        dependencies = architectural_dependencies(
            imported_modules(
                source,
                python_file,
                root_package=root_package,
                known_modules=known_modules,
            )
        )
        nloc = source_nloc(source)
        reasons: list[str] = []
        if len(dependencies) > architectural_max:
            reasons.append(
                f"architectural imports {len(dependencies)} > {architectural_max}"
            )
        if (
            nloc > large_module_nloc
            and len(dependencies) > large_module_architectural_max
        ):
            reasons.append(
                f"large module {nloc} NLOC with architectural imports "
                f"{len(dependencies)} > {large_module_architectural_max}"
            )
        results.append(
            FanoutResult(
                relpath=python_file.relpath,
                count=len(dependencies),
                limit=architectural_max,
                nloc=nloc,
                dependencies=tuple(sorted(dependencies)),
                reasons=tuple(reasons),
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--root-package", default="raidar")
    parser.add_argument("--architectural-max", type=int, default=10)
    parser.add_argument("--large-module-nloc", type=int, default=800)
    parser.add_argument("--large-module-architectural-max", type=int, default=3)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    results = analyse_fanout(
        repo_root,
        root_package=args.root_package,
        architectural_max=args.architectural_max,
        large_module_nloc=args.large_module_nloc,
        large_module_architectural_max=args.large_module_architectural_max,
    )

    failures = [result for result in results if result.reasons]
    if not failures:
        print(f"[fanout] OK: {len(results)} Python files checked")
        return 0

    print("[fanout] Architectural fan-out limit exceeded:", file=sys.stderr)
    for result in sorted(failures, key=lambda item: (-item.count, item.relpath)):
        dependencies = ", ".join(result.dependencies)
        reasons = "; ".join(result.reasons)
        print(
            f"  {result.relpath}: {reasons} ({dependencies})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
