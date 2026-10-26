"""Workspace snapshot identity: one exclude list, one digest.

The same exclude list is used for Harbor's per-step ``/app`` artifact (which
the verifier grades by default, ``snapshot_source=artifact``) and for the
downloaded copy (``snapshot_source=download``), so both sources hash identically.
Patterns match any path component, like the ``tar --exclude`` flags Harbor passes.
``.raidar`` is a workflow's own state in the workspace (design section 5.1: a workflow's
own files are never graded, diffed or scored).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

APP_SOURCE = "/app"
APP_ARTIFACT_DESTINATION = "app"
SNAPSHOT_EXCLUDES: tuple[str, ...] = (
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
)


def app_artifact_config() -> dict[str, object]:
    """The ``[[steps]].artifacts`` entry that yields the gradable snapshot."""
    return {
        "source": APP_SOURCE,
        "destination": APP_ARTIFACT_DESTINATION,
        "exclude": list(SNAPSHOT_EXCLUDES),
    }


def is_excluded(relative: PurePosixPath) -> bool:
    return any(part in SNAPSHOT_EXCLUDES for part in relative.parts)


def snapshot_files(root: Path) -> list[Path]:
    """Files and symlinks under ``root`` that belong to the snapshot, sorted."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_raise_walk_error):
        base = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if not is_excluded(PurePosixPath((base / d).relative_to(root)))
        )
        for name in filenames:
            path = base / name
            if not is_excluded(PurePosixPath(path.relative_to(root))):
                found.append(path)
        found.extend(base / d for d in dirnames if (base / d).is_symlink())
    return sorted(found, key=lambda p: p.relative_to(root).as_posix().encode())


def _raise_walk_error(error: OSError) -> None:
    raise error


def _regular_file_digest(path: Path) -> bytes:
    """Hash only regular files, without following a replacement symlink or blocking on a FIFO."""
    if not stat.S_ISREG(path.lstat().st_mode):
        raise OSError(f"snapshot entry is not a regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError(f"snapshot entry is not a regular file: {path}")
        return hashlib.file_digest(stream, "sha256").digest()


def tree_digest(root: Path) -> str:
    """Order-stable content digest (relative path, kind, exec bit, bytes or link target).

    The executable bit is part of the identity because ``copy_snapshot``
    preserves it and it can change a verdict (a black-box entrypoint that is
    or is not executable). Read/write bits are not: they differ between the
    artifact and download sources and never change what the grader runs.
    """
    digest = hashlib.sha256(b"raidar-snapshot\0")
    for path in snapshot_files(root):
        relative = os.fsencode(path.relative_to(root).as_posix())
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            kind, content = b"L", os.fsencode(os.readlink(path))
        else:
            kind = b"X" if mode & stat.S_IXUSR else b"F"
            content = _regular_file_digest(path)
        digest.update(kind)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def copy_snapshot(source: Path, dest: Path) -> None:
    """Copy a snapshot without excluded paths, preserving symlinks."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SNAPSHOT_EXCLUDES}

    shutil.copytree(source, dest, symlinks=True, ignore=ignore, dirs_exist_ok=True)
