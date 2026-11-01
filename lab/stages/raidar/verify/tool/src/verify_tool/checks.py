"""The gate's three file checks, run exactly as the spike gate ran them.

Each check is given every Python file of the work tree outside the excluded directories (the
workspace caches and the workflow's own files), sorted; a tree with no Python file passes. ruff
and mypy come from this package's installed environment, whose interpreter runs this module. mypy
reads the project's own environment when it has one (``.venv``). Their caches live in a fresh
temporary directory, never in the workspace. The exit code is the tool's own: zero passes and any
other code refuses; a tool killed by a signal exits 126, which the gate treats as an error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

EXCLUDES = (
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".git",
    "node_modules",
    "target",
    "dist",
    "build",
    ".raidar",
    ".agents",
    ".claude",
)
NO_FILES = "no Python files to check"
CRASHED = 126
CHECKS = {
    "lint": ("ruff", "check"),
    "format": ("ruff", "format", "--check"),
    "types": ("mypy", "--strict"),
}


def python_files(root: Path, excludes: Sequence[str] = EXCLUDES) -> list[str]:
    """Every ``.py`` file under ``root`` outside ``excludes``, relative and sorted."""
    found: list[str] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excludes)
        base = Path(current)
        found += [
            (base / name).relative_to(root).as_posix() for name in names if name.endswith(".py")
        ]
    return sorted(found)


def command(check: str, root: Path, files: Sequence[str]) -> list[str]:
    """The argv of ``check`` over ``files``, on this environment's interpreter."""
    tool, *arguments = CHECKS[check]
    extra: list[str] = []
    project_python = root / ".venv" / "bin" / "python"
    if check == "types" and project_python.exists():
        extra = ["--python-executable", str(project_python)]
    return [sys.executable, "-I", "-B", "-m", tool, *arguments, *extra, *files]


def run(check: str, root: Path) -> int:
    """Run ``check`` in ``root``; return its exit code."""
    files = python_files(root)
    if not files:
        sys.stdout.write(f"{NO_FILES}\n")
        return 0
    with tempfile.TemporaryDirectory(prefix="verify-") as cache:
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "RUFF_CACHE_DIR": str(Path(cache) / "ruff"),
            "MYPY_CACHE_DIR": str(Path(cache) / "mypy"),
        }
        sys.stdout.flush()
        done = subprocess.run(command(check, root, files), cwd=root, env=env, check=False)
    return done.returncode if done.returncode >= 0 else CRASHED


def main(argv: Sequence[str] | None = None) -> int:
    """Run the named check in the current directory (the workspace)."""
    parser = argparse.ArgumentParser(prog="verify")
    parser.add_argument("check", choices=sorted(CHECKS))
    args = parser.parse_args(argv)
    return run(args.check, Path.cwd())
