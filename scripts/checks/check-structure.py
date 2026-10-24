#!/usr/bin/env python3
"""Fail when the repository layout breaks the structure rules.

Rules (docs/reboot/repo-structure-design.md §3.5):

* no repository path carries a version token (``_v2``, ``-v2``, a ``v2`` segment); the
  version is data (``name@N`` in a manifest). ``v<N>/`` data folders under a
  ``components/`` directory or under ``lab/treatment/`` are the one exception;
* the repository root holds only the allowlisted directories and files;
* ``lab/`` holds only the allowlisted data trees (experiments, problem pins, the ledger, component
  payloads, runtime-pack recipes and judge policies);
* ``orchestrator/src/raidar`` holds only the allowlisted stage packages, and each holds only its
  allowlisted subpackages (§8);
* packages nest at most three levels below ``raidar/`` (run-time data lives under ``lab/``, not
  in the package).

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
# The data trees the code loads at run time, each at one explicit path (contract/paths.py).
LAB_DIRECTORIES = frozenset(
    {"experiments", "problems", "ledger", "treatment", "environment", "judge"}
)
RAIDAR_PACKAGE_DIR = "orchestrator/src/raidar"
# Top-level packages (the stages of an evaluation, plus the CLI and the shared contracts), each with
# the subpackages its group may hold (docs/reboot/repo-structure-design.md §8). Modules sit freely
# at either level; a group's subpackage outside its set is refused.
RAIDAR_PACKAGES: dict[str, frozenset[str]] = {
    "cli": frozenset(),
    "contract": frozenset(),
    "design": frozenset({"experiment", "problem"}),
    "subject": frozenset({"agent", "treatment", "environment"}),
    "engine": frozenset({"target", "job", "trial", "health", "perf"}),
    "grading": frozenset({"runtime"}),
    "scoring": frozenset(
        {
            "framework",
            "correctness",
            "code_quality",
            "process_checks",
            "gate_effort",
            "resource_usage",
            "judge",
        }
    ),
    "evaluation": frozenset({"analysis", "review", "ledger"}),
}
MAX_PACKAGE_DEPTH = 3
VERSION_TOKEN = re.compile(r"(?<![A-Za-z0-9])v\d+(?![A-Za-z0-9])")
DATA_VERSION_SEGMENT = re.compile(r"v\d+")
DATA_PARENT = "components"
DATA_PREFIX = "lab/treatment/"
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
        and (DATA_PARENT in parts[:index] or "/".join(parts[:2]) + "/" == DATA_PREFIX)
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


def lab_violations(paths: list[str]) -> list[str]:
    found = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if parts[0] == "lab" and len(parts) > 1 and parts[1] not in LAB_DIRECTORIES:
            found.add(f"unexpected lab entry: lab/{parts[1]}")
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


def violations(paths: list[str]) -> list[str]:
    return [
        *root_violations(paths),
        *lab_violations(paths),
        *package_violations(paths),
        *version_violations(paths),
    ]


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
