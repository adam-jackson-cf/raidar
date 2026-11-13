"""Git-owned file inventory for repository checks."""

from __future__ import annotations

import subprocess
from pathlib import Path


def repo_paths(repo_root: Path) -> list[str]:
    """List tracked and non-ignored untracked paths without walking directories."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
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
        }
    )


def python_paths(repo_root: Path) -> list[str]:
    """Filter the maintained inventory to Python paths."""
    return [path for path in repo_paths(repo_root) if path.endswith(".py")]
