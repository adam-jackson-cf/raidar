#!/usr/bin/env python3
"""Fail when the repository layout breaks the structure rules.

Rules (docs/reboot/repo-structure-design.md §3.5):

* no repository path carries a version token (``_v2``, ``-v2``, a ``v2`` segment); the
  version is data (``name@N`` in a manifest). ``v<N>/`` data folders under a
  ``components/`` directory are the one exception;
* the repository root holds only the allowlisted directories and files;
* ``orchestrator/src/raidar`` holds only the allowlisted packages;
* packages nest at most three levels below ``raidar/`` (``components/payloads/**`` data and
  ``verifier/runtime`` are exempt).

Paths are the tracked files plus untracked files that are not git-ignored.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT_DIRECTORIES = frozenset(
    {
        "orchestrator",
        "review-surface",
        "lab",
        "docs",
        "scripts",
        ".agents",
        ".claude",
        ".github",
        "var",
    }
)
ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".mailmap",
        ".pre-commit-config.yaml",
        "AGENTS.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CODEOWNERS",
        "Makefile",
        "README.md",
        "skills-lock.json",
    }
)
RAIDAR_PACKAGE_DIR = "orchestrator/src/raidar"
RAIDAR_PACKAGES = frozenset(
    {
        "agents",
        "analysis",
        "cli",
        "components",
        "core",
        "execution",
        "jev",
        "ledger",
        "packs",
        "problems",
        "review",
        "scorers",
        "spec",
        "targets",
        "verifier",
    }
)
MAX_PACKAGE_DEPTH = 3
# Data and runtime trees below raidar/ that are not importable package structure.
DEPTH_EXEMPT_PREFIXES = (
    "orchestrator/src/raidar/components/payloads/",
    "orchestrator/src/raidar/verifier/runtime/",
)
VERSION_TOKEN = re.compile(r"(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])")
DATA_VERSION_SEGMENT = re.compile(r"v\d+")
DATA_PARENT = "components"
# Dated design records keep their historical file names (the docs tree is not product layout).
VERSION_EXEMPT_PREFIXES = ("docs/",)


def repo_paths(repo_root: Path) -> list[str]:
    """Tracked files plus untracked, not-ignored files, as repo-relative posix paths."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git ls-files failed")
    return sorted({line for line in result.stdout.splitlines() if line})


def _is_data_version_dir(parts: tuple[str, ...], index: int) -> bool:
    """A ``v<N>`` directory segment below a ``components`` directory."""
    return (
        index < len(parts) - 1
        and DATA_VERSION_SEGMENT.fullmatch(parts[index]) is not None
        and DATA_PARENT in parts[:index]
    )


def version_violations(paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        if path.startswith(VERSION_EXEMPT_PREFIXES):
            continue
        parts = PurePosixPath(path).parts
        for index, part in enumerate(parts):
            if VERSION_TOKEN.search(part) and not _is_data_version_dir(parts, index):
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


def package_violations(paths: list[str]) -> list[str]:
    found = []
    prefix = RAIDAR_PACKAGE_DIR + "/"
    packages: set[str] = set()
    for path in paths:
        if not path.startswith(prefix):
            continue
        parts = PurePosixPath(path[len(prefix) :]).parts
        if len(parts) > 1:
            packages.add(parts[0])
        if len(parts) - 1 > MAX_PACKAGE_DEPTH and not path.startswith(DEPTH_EXEMPT_PREFIXES):
            found.append(
                f"package nests deeper than {MAX_PACKAGE_DEPTH} below raidar/: "
                f"{PurePosixPath(path).parent}"
            )
    found.extend(
        f"unexpected raidar package: {name}/" for name in sorted(packages - RAIDAR_PACKAGES)
    )
    return sorted(set(found))


def violations(paths: list[str]) -> list[str]:
    return [*root_violations(paths), *package_violations(paths), *version_violations(paths)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    found = violations(repo_paths(args.repo_root.resolve()))
    if not found:
        print("structure-check: ok")
        return 0
    print("structure-check: the repository layout breaks the structure rules:", file=sys.stderr)
    for line in found:
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
