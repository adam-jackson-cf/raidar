"""Content identity and leak checks for imported problem content.

``content_digest`` hashes every file under a root. Unlike the workspace
snapshot digest (``raidar.contracts.snapshot.tree_digest``) it has no exclude
list: a task, verifier bundle or reference payload is input content (N3), so a
file under ``build/``, ``dist/`` or ``__pycache__/`` is part of its identity.
The encoding is otherwise the same, so trees without such directories hash
identically under both.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from raidar.grading.bundle import VerifierBundle

HIDDEN_FILE_NAMES = frozenset({"conftest.py", "solve.sh", "_ctrf_to_reward.py", "test.sh"})


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        found.extend(base / name for name in filenames)
        found.extend(base / d for d in dirnames if (base / d).is_symlink())
    return sorted(found, key=lambda p: p.relative_to(root).as_posix().encode())


def content_digest(root: Path) -> str:
    """Order-stable digest of every file (relative path, kind, exec bit, bytes or link)."""
    digest = hashlib.sha256()
    for path in _files(root):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        else:
            executable = path.stat().st_mode & stat.S_IXUSR
            digest.update((b"X" if executable else b"F") + path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def hidden_content_in_task(task_dir: Path, bundle: VerifierBundle) -> list[str]:
    """Task files that are, or byte-match, hidden verifier content (must be empty)."""
    hidden = bundle.hidden_file_hashes()
    stub = task_dir / "tests" / "test.sh"
    found: list[str] = []
    for path in sorted(p for p in task_dir.rglob("*") if p.is_file()):
        relative = path.relative_to(task_dir).as_posix()
        by_name = path != stub and (
            path.name in HIDDEN_FILE_NAMES
            or path.name.startswith("test_")
            or "solution" in path.parts
        )
        if by_name or hashlib.sha256(path.read_bytes()).hexdigest() in hidden:
            found.append(relative)
    return found
