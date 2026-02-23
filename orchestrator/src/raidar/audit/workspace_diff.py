"""Workspace diff and fingerprint helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUDED_DIRS: tuple[str, ...] = (
    ".git",
    "__pycache__",
    "node_modules",
    ".next",
    ".turbo",
    ".cache",
    "coverage",
    "dist",
    "build",
    "tmp",
    "jobs",
    "harbor-task",
)
DEFAULT_EXCLUDED_FILES: tuple[str, ...] = (
    ".DS_Store",
    "actual.png",
    "diff.png",
)


@dataclass(frozen=True, slots=True)
class WorkspaceDiff:
    """Directory-level file changes from baseline to current."""

    added: list[str]
    removed: list[str]
    modified: list[str]

    @property
    def changed_files(self) -> list[str]:
        changes: list[str] = []
        changes.extend(f"Added: {path}" for path in self.added)
        changes.extend(f"Removed: {path}" for path in self.removed)
        changes.extend(f"Modified: {path}" for path in self.modified)
        return changes

    @property
    def count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)


def _iter_files(
    root: Path,
    *,
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRS,
    exclude_files: tuple[str, ...] = DEFAULT_EXCLUDED_FILES,
) -> list[Path]:
    files: list[Path] = []
    excluded_dirs = set(exclude_dirs)
    excluded_files = set(exclude_files)
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(segment in excluded_dirs for segment in rel.parts):
            continue
        if path.is_file() and path.name not in excluded_files:
            files.append(path)
    files.sort(key=lambda item: str(item.relative_to(root)))
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def directory_hashes(
    root: Path,
    *,
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRS,
    exclude_files: tuple[str, ...] = DEFAULT_EXCLUDED_FILES,
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for file_path in _iter_files(root, exclude_dirs=exclude_dirs, exclude_files=exclude_files):
        relative = str(file_path.relative_to(root))
        hashes[relative] = _sha256(file_path)
    return hashes


def directory_fingerprint(
    root: Path,
    *,
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRS,
    exclude_files: tuple[str, ...] = DEFAULT_EXCLUDED_FILES,
) -> str:
    hashes = directory_hashes(root, exclude_dirs=exclude_dirs, exclude_files=exclude_files)
    seed = "|".join(f"{path}:{hash_value}" for path, hash_value in sorted(hashes.items()))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def diff_directories(
    baseline: Path,
    current: Path,
    *,
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_DIRS,
    exclude_files: tuple[str, ...] = DEFAULT_EXCLUDED_FILES,
) -> WorkspaceDiff:
    baseline_hashes = directory_hashes(
        baseline, exclude_dirs=exclude_dirs, exclude_files=exclude_files
    )
    current_hashes = directory_hashes(
        current, exclude_dirs=exclude_dirs, exclude_files=exclude_files
    )

    baseline_paths = set(baseline_hashes)
    current_paths = set(current_hashes)
    added = sorted(current_paths - baseline_paths)
    removed = sorted(baseline_paths - current_paths)
    modified = sorted(
        path
        for path in baseline_paths & current_paths
        if baseline_hashes[path] != current_hashes[path]
    )
    return WorkspaceDiff(added=added, removed=removed, modified=modified)
