"""Which workspace files the gate checks (adapted from Enaible python_quality_scope).

Enaible's fixed maintained roots and allowlists are replaced by one rule: the
workspace root is the one maintained root. Excluded are local environments,
caches, VCS metadata and build output. A test file is ``test_*.py``,
``*_test.py``, ``conftest.py`` or any file under a ``test``/``tests``/
``testing``/``__tests__`` directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
    }
)
TEST_SEGMENTS: frozenset[str] = frozenset({"test", "tests", "testing", "__tests__"})


@dataclass(frozen=True)
class SourceFile:
    """One Python file in the workspace."""

    path: Path
    relpath: str
    is_test: bool


def is_excluded(relpath: str) -> bool:
    """Return whether any path segment is an excluded directory."""
    parts = PurePosixPath(relpath).parts
    return any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts)


def is_test_relpath(relpath: str) -> bool:
    """Return whether the file is a test file (see the module docstring)."""
    parts = PurePosixPath(relpath).parts
    name = parts[-1]
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(part in TEST_SEGMENTS for part in parts[:-1])


def discover(root: Path) -> list[SourceFile]:
    """Return every non-excluded ``.py`` file under root, without following symlinks."""
    found: list[SourceFile] = []
    for current, dirnames, filenames in os.walk(root):
        base = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if not is_excluded(name) and not (base / name).is_symlink()
        )
        for name in sorted(filenames):
            path = base / name
            if not name.endswith(".py") or path.is_symlink():
                continue
            relpath = path.relative_to(root).as_posix()
            found.append(SourceFile(path, relpath, is_test_relpath(relpath)))
    return found
