#!/usr/bin/env python3
"""Run affected local T2 seams, conservatively falling back to the complete tier."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from git_inventory import repo_paths, untracked_paths

ROOT = Path(__file__).resolve().parents[2]


def _safe_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts) and not path.startswith("/") and ".." not in parts and "\\" not in path


def select_affected_tests(
    changed_paths: Sequence[str],
    seam_tests: Sequence[str],
    owners: Mapping[str, Sequence[str]],
) -> list[str]:
    """Union boundary-matched owners; unknown or stale ownership runs every seam."""
    inventory = set(seam_tests)
    selected: set[str] = set()
    for path in changed_paths:
        if not _safe_path(path):
            return sorted(inventory)
        matched = path in inventory
        if matched:
            selected.add(path)
        for prefix, tests in owners.items():
            owner = prefix.rstrip("/")
            if path == owner or path.startswith(owner + "/"):
                matched = True
                if not tests or not set(tests) <= inventory:
                    return sorted(inventory)
                selected.update(tests)
        if not matched:
            return sorted(inventory)
    return sorted(selected)


def file_markers(tree: ast.Module) -> set[str]:
    """Read file-level pytest markers without importing test modules."""
    markers: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            markers.update(
                item.attr
                for item in ast.walk(node.value)
                if isinstance(item, ast.Attribute)
                and isinstance(item.value, ast.Attribute)
                and ast.unparse(item.value) == "pytest.mark"
            )
    return markers


def discover_seams(paths: Sequence[str], root: Path) -> list[str]:
    """Discover file-level T2 markers rather than trusting a stale owner inventory."""
    seams = []
    for path in paths:
        if not path.endswith(".py") or not (
            "/tests/" in path or path.startswith("contract/conformance/")
        ):
            continue
        source = root / path
        if not source.is_file():
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=path)
        markers = file_markers(tree)
        if markers & {"slow", "quality"} and "docker" not in markers:
            seams.append(path)
    return sorted(seams)


def main() -> int:
    """Read staged/worktree changes and invoke pytest once for the selected seams."""
    inventory = repo_paths(ROOT)
    changed = (
        subprocess.run(
            ["git", "diff", "--name-only", "-z", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        )
        .stdout.decode()
        .split("\0")
    )
    untracked = untracked_paths(ROOT)
    policy = json.loads((ROOT / "scripts/checks/repository-owners.json").read_text())
    seams = discover_seams([path for path in inventory if path], ROOT)
    selected = select_affected_tests(
        [path for path in changed + untracked if path], seams, policy["seam_owners"]
    )
    if not selected:
        print("TEST.tiers: no affected local T2 seams")
        return 0
    print(f"TEST.tiers: running {len(selected)} affected T2 files", flush=True)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "(slow or quality) and not docker",
            *[str(ROOT / path) for path in selected],
        ],
        cwd=ROOT / "orchestrator",
        check=False,
        timeout=900,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
