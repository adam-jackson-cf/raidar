"""Which workspace files the gate checks (adapted from Enaible python_quality_scope).

Enaible's fixed maintained roots and allowlists are replaced by one rule: the
workspace root is the one maintained root. Excluded are local environments,
caches, VCS metadata and build output. A test file is ``test_*.py``,
``*_test.py``, ``conftest.py`` or any file under a ``test``/``tests``/
``testing``/``__tests__`` directory.

python-guardrails@4 widens what counts as a Python file (@3 saw only regular
``*.py`` files, so a workspace whose program was an extensionless script with a
``.py`` symlink to it was checked as empty):

- an extensionless regular file whose first line is a ``#!`` line naming
  ``python`` (a script entrypoint such as ``./tool``) is a Python file;
- a ``*.py`` symlink whose target resolves to a regular file inside the root
  counts as that target (the target's own path is checked, once, whatever
  the target's name). A symlink that leaves the root, or that resolves to a
  directory or nothing, is skipped, and symlinked directories are still not
  followed.
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


SHEBANG_PREFIX = b"#!"
SHEBANG_READ_BYTES = 256


def is_python_script(path: Path) -> bool:
    """An extensionless regular file whose first line is a ``#!`` line naming python."""
    if path.suffix or path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            first = handle.readline(SHEBANG_READ_BYTES)
    except OSError:
        return False
    return first.startswith(SHEBANG_PREFIX) and b"python" in first


def _symlink_target(root: Path, path: Path) -> Path | None:
    """The in-root regular file a ``*.py`` symlink resolves to (None: skipped)."""
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    resolved_root = root.resolve()
    if not target.is_file() or not target.is_relative_to(resolved_root):
        return None
    relpath = target.relative_to(resolved_root).as_posix()
    return None if is_excluded(relpath) else root / relpath


def _candidates(root: Path) -> tuple[list[Path], list[Path]]:
    """Regular Python files (``*.py`` and shebang scripts) and ``*.py`` symlinks."""
    files: list[Path] = []
    links: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        base = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if not is_excluded(name) and not (base / name).is_symlink()
        )
        for name in sorted(filenames):
            path = base / name
            if name.endswith(".py"):
                (links if path.is_symlink() else files).append(path)
            elif is_python_script(path):
                files.append(path)
    return files, links


def discover(root: Path) -> list[SourceFile]:
    """Every non-excluded Python file under root (see the module docstring), once each."""
    files, links = _candidates(root)
    seen = {path.resolve() for path in files}
    for link in links:
        target = _symlink_target(root, link)
        if target is not None and target.resolve() not in seen:
            seen.add(target.resolve())
            files.append(target)
    found = []
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relpath = path.relative_to(root).as_posix()
        found.append(SourceFile(path, relpath, is_test_relpath(relpath)))
    return found
