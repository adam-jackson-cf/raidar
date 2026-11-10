#!/usr/bin/env python3
"""Fail when the repository layout breaks the structure rules.

Rules (docs/reboot/repo-structure-design.md §3.5):

* no repository path carries a version token (``_v2``, ``-v2``, a ``v2`` segment); the
  version is data (``name@N`` in a manifest);
* the repository root holds only the allowlisted directories and files;
* ``lab/`` holds only the allowlisted data trees (experiments, problem pins, the ledger, agent
  and workflow definitions, runtime-pack recipes and judge policies);
* ``orchestrator/src/raidar`` holds only the allowlisted stage packages, and each holds only its
  allowlisted subpackages (§8);
* packages nest at most three levels below ``raidar/`` (run-time data lives under ``lab/``, not
  in the package).

Paths are the tracked files plus untracked files that are not git-ignored.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from raidar.subject.package.registry import load_stage_package
from raidar_contract.package import StagePackageError

ROOT_DIRECTORIES = frozenset(
    {
        "contract",
        "orchestrator",
        "review-surface",
        "lab",
        "docs",
        "scripts",
        ".agents",
        ".claude",
        ".github",
        ".omp",
        "var",
    }
)
ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".mailmap",
        ".pre-commit-config.yaml",
        ".python-version",
        "AGENTS.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CODEOWNERS",
        "Makefile",
        "README.md",
        "package.json",
        "package-lock.json",
        "skills-lock.json",
        "split-design.md",
    }
)
# The data trees the code loads at run time, each at one explicit path (contract/paths.py).
LAB_DIRECTORIES = frozenset(
    {
        "experiments",
        "problems",
        "ledger",
        "agents",
        "workflows",
        "stages",
        "stage-checks",
        "environments",
        "judge",
    }
)
# Release register and identity-free, unverified import provenance.
LAB_FILES = frozenset({"releases.yaml", "imports.yaml"})
RAIDAR_PACKAGE_DIR = "orchestrator/src/raidar"
# Top-level packages (the stages of an evaluation, plus the CLI and the shared contracts), each with
# the subpackages its group may hold (docs/reboot/repo-structure-design.md §8). Modules sit freely
# at either level; a group's subpackage outside its set is refused.
RAIDAR_PACKAGES: dict[str, frozenset[str]] = {
    "cli": frozenset(),
    "contract": frozenset(),
    "judge": frozenset(),
    "design": frozenset({"experiment", "problem"}),
    "subject": frozenset({"agent", "workflow", "environment", "package", "primitive"}),
    "engine": frozenset({"target", "job", "trial", "health", "perf"}),
    "grading": frozenset({"runtime", "pre_fix", "coverage"}),
    "scoring": frozenset(
        {
            "catalog",
            "framework",
            "correctness",
            "code_quality",
            "process_checks",
            "verification_effort",
            "resource_usage",
            "judge",
            "coverage_inputs",
            "definition_coverage",
            "test_coverage",
        }
    ),
    "evaluation": frozenset({"analysis", "review", "ledger"}),
}
CONTRACT_FORBIDDEN_IMPORTS = frozenset({"raidar", "harbor"})
MAX_PACKAGE_DEPTH = 3
VERSION_TOKEN = re.compile(r"(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])")
# Dated design records keep their historical file names (the docs tree is not product layout).
VERSION_EXEMPT_PREFIXES = ("docs/",)


def repo_paths(repo_root: Path) -> list[str]:
    """Tracked files plus untracked, not-ignored files, as repo-relative posix paths."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git ls-files failed")
    return sorted({line for line in result.stdout.splitlines() if line})


def version_violations(paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        if path.startswith(VERSION_EXEMPT_PREFIXES):
            continue
        parts = PurePosixPath(path).parts
        for index, part in enumerate(parts):
            if VERSION_TOKEN.search(part):
                found.append(f"version in path name: {path}")
                break
    return found


def root_violations(paths: list[str]) -> list[str]:
    found = []
    seen: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        top = parts[0]
        if top in seen:
            continue
        seen.add(top)
        if len(parts) == 1 and top not in ROOT_FILES:
            found.append(f"unexpected root file: {top}")
        elif len(parts) > 1 and top not in ROOT_DIRECTORIES:
            found.append(f"unexpected top-level directory: {top}/")
    return found


def lab_violations(paths: list[str]) -> list[str]:
    found = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if parts[0] != "lab" or len(parts) < 2:
            continue
        allowed = LAB_FILES if len(parts) == 2 else LAB_DIRECTORIES
        if parts[1] not in allowed:
            found.add(f"unexpected lab entry: lab/{parts[1]}")
    return sorted(found)


def workflow_violations(paths: list[str]) -> list[str]:
    """Workflows live at ``lab/workflows/<ns>/<name>/``: nothing sits at the data root or a
    namespace folder, and each workflow folder holds a ``workflow.yaml``."""
    found = set()
    folders: set[tuple[str, ...]] = set()
    defined: set[tuple[str, ...]] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if parts[:2] != ("lab", "workflows"):
            continue
        if len(parts) < 5:
            found.add(f"workflow outside lab/workflows/<ns>/<name>/: {path}")
            continue
        folders.add(parts[:4])
        if len(parts) == 5 and parts[4] == "workflow.yaml":
            defined.add(parts[:4])
    found.update(
        f"workflow folder without workflow.yaml: {'/'.join(folder)}"
        for folder in sorted(folders - defined)
    )
    return sorted(found)


def package_violations(paths: list[str]) -> list[str]:
    found = []
    prefix = RAIDAR_PACKAGE_DIR + "/"
    for path in paths:
        if not path.startswith(prefix):
            continue
        parts = PurePosixPath(path[len(prefix) :]).parts
        if len(parts) > 1 and parts[0] not in RAIDAR_PACKAGES:
            found.append(f"unexpected raidar package: {parts[0]}/")
        elif len(parts) > 2 and parts[1] not in RAIDAR_PACKAGES[parts[0]]:
            found.append(f"unexpected {parts[0]} subpackage: {parts[0]}/{parts[1]}/")
        if len(parts) - 1 > MAX_PACKAGE_DEPTH:
            found.append(
                f"package nests deeper than {MAX_PACKAGE_DEPTH} below raidar/: "
                f"{PurePosixPath(path).parent}"
            )
    return sorted(set(found))


def _stage_roots(entries: list[PurePosixPath]) -> list[PurePosixPath]:
    prefix = PurePosixPath("lab/stages")
    candidates = sorted(
        {
            path.parent
            for path in entries
            if path.name == "stage.yaml" and len(path.relative_to(prefix).parts) == 3
        },
        key=lambda path: (len(path.parts), str(path)),
    )
    roots: list[PurePosixPath] = []
    for candidate in candidates:
        if not any(candidate.is_relative_to(root) for root in roots):
            roots.append(candidate)
    return roots


def stage_violations(paths: list[str], repo_root: Path) -> list[str]:
    """Check the ``stage/1`` package layout without importing authored code."""
    entries = [
        PurePosixPath(path)
        for path in paths
        if path.startswith("lab/stages/") and path != "lab/stages/README.md"
    ]
    roots = _stage_roots(entries)
    found = [
        f"stage file outside a declared package: {path}"
        for path in entries
        if not any(path.is_relative_to(root) for root in roots)
    ]
    for root in roots:
        try:
            load_stage_package(repo_root / str(root))
        except (StagePackageError, OSError) as exc:
            found.append(f"invalid stage package {root}: {exc}")
    return found


def contract_violations(paths: list[str], repo_root: Path) -> list[str]:
    """``raidar_contract`` imports neither ``raidar`` nor Harbor (it ships on its own)."""
    found = []
    for path in paths:
        if not (path.startswith("contract/src/") and path.endswith(".py")):
            continue
        tree = ast.parse((repo_root / path).read_bytes(), filename=path)
        found.extend(_contract_import_violations(path, tree))
    return found


def _contract_import_violations(path: str, tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        found.extend(
            f"{path}:{node.lineno}: the contract never imports {name}"
            for name in names
            if name.split(".")[0] in CONTRACT_FORBIDDEN_IMPORTS
        )
    return found


def violations(paths: list[str]) -> list[str]:
    return [
        *root_violations(paths),
        *lab_violations(paths),
        *package_violations(paths),
        *workflow_violations(paths),
        *version_violations(paths),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    paths = repo_paths(args.repo_root.resolve())
    found = [
        *violations(paths),
        *stage_violations(paths, args.repo_root),
        *contract_violations(paths, args.repo_root.resolve()),
    ]
    if not found:
        print("structure-check: ok")
        return 0
    print(
        "structure-check: the repository layout breaks the structure rules:",
        file=sys.stderr,
    )
    for line in found:
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
