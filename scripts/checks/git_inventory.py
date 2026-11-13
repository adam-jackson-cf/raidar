"""Git-owned file inventory for repository checks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _present(repo_root: Path, relative: str) -> bool:
    try:
        os.lstat(repo_root / relative)
    except FileNotFoundError:
        return False
    return True


def _git_paths(repo_root: Path, options: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", *options],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.stderr.decode(errors="replace").strip() or "git ls-files failed")
    return sorted(
        {
            path
            for raw in result.stdout.split(b"\0")
            if raw and (path := raw.decode(errors="surrogateescape"))
            and _present(repo_root, path)
        }
    )


def repo_paths(repo_root: Path) -> list[str]:
    """List maintained paths present in the worktree without following symlinks."""
    return _git_paths(repo_root, ["--cached", "--others", "--exclude-standard"])


def untracked_paths(repo_root: Path) -> list[str]:
    """List present, nonignored untracked paths without following symlinks."""
    return _git_paths(repo_root, ["--others", "--exclude-standard"])


def python_paths(repo_root: Path) -> list[str]:
    """Filter the maintained inventory to Python paths."""
    return [path for path in repo_paths(repo_root) if path.endswith(".py")]
