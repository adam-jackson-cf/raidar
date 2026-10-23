"""Workspace snapshot identity: one exclude list, one digest.

The same exclude list is used for Harbor's per-step ``/app`` artifact (which
the verifier grades by default, ``snapshot_source=artifact``) and for the
download fallback, so both sources hash identically (overhead-baseline §4:
"keep them in one place"). Patterns match any path component, like the
``tar --exclude`` flags Harbor passes.
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
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
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


def tree_digest(root: Path) -> str:
    """Order-stable content digest (relative path, kind, exec bit, bytes or link target).

    The executable bit is part of the identity because ``copy_snapshot``
    preserves it and it can change a verdict (a black-box entrypoint that is
    or is not executable). Read/write bits are not: they differ between the
    artifact and download sources and never change what the grader runs.
    """
    digest = hashlib.sha256()
    for path in snapshot_files(root):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        else:
            executable = path.stat().st_mode & stat.S_IXUSR
            digest.update((b"X" if executable else b"F") + path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def copy_snapshot(source: Path, dest: Path) -> None:
    """Copy a snapshot without excluded paths, preserving symlinks."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in SNAPSHOT_EXCLUDES}

    shutil.copytree(source, dest, symlinks=True, ignore=ignore, dirs_exist_ok=True)


def make_world_writable(root: Path) -> None:
    """Let the sandbox's non-root user write to a staged host copy."""
    for dirpath, _dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o777)
        for name in filenames:
            path = Path(dirpath) / name
            if not path.is_symlink():
                os.chmod(path, 0o666 | (path.stat().st_mode & 0o111))
