"""``stage-tree/1``: the digest of a package's exact bytes and executable bits.

Fixtures do not ship and are excluded; ``.git``, ``__pycache__`` and ``.DS_Store`` are ignored.
Symlinks (fixture trees included), special files and non-UTF-8 names are refused. Nothing in the
tree is opened except regular files, and the walk never follows a link.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from operator import attrgetter
from pathlib import Path

from raidar_contract.canonical import IGNORED_TREE_NAMES, digest_json, sha256_hex
from raidar_contract.package import StagePackageError

TREE_SCHEME = "stage-tree/1"
FIXTURES_DIR = "fixtures"
_EXEC_BITS = 0o111
"""Any of the owner, group or other execute bits makes a file executable in the digest."""


@dataclass(frozen=True)
class StageFile:
    """One shipped file: its package-relative POSIX path, exact bytes and executable bit."""

    path: str
    sha256: str
    executable: bool
    data: bytes = field(repr=False, compare=False)


def _read_file(entry: os.DirEntry[str], relative: str) -> StageFile:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(entry.path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise StagePackageError(f"{relative}: special files are refused")
        data = handle.read()
    executable = bool(info.st_mode & _EXEC_BITS)
    return StageFile(relative, sha256_hex(data), executable, data)


def _entries(directory: str) -> list[os.DirEntry[str]]:
    with os.scandir(directory) as scan:
        entries = sorted(scan, key=attrgetter("name"))
    for entry in entries:
        if entry.is_symlink():
            raise StagePackageError(f"{entry.path}: symlinks are refused")
        if not (entry.is_dir(follow_symlinks=False) or entry.is_file(follow_symlinks=False)):
            raise StagePackageError(f"{entry.path}: special files are refused")
    return entries


def _walk(root: Path) -> list[StageFile]:
    """Walk excluded entries too, checking safety without reading their bytes."""
    files: list[StageFile] = []
    pending = [(str(root), "", True)]
    while pending:
        directory, base, shipped = pending.pop()
        for entry in _entries(directory):
            relative = f"{base}{entry.name}"
            _require_utf8(relative)
            include = shipped and entry.name not in IGNORED_TREE_NAMES and relative != FIXTURES_DIR
            if entry.is_dir(follow_symlinks=False):
                pending.append((entry.path, f"{relative}/", include))
            elif include:
                files.append(_read_file(entry, relative))
    return files


def _require_utf8(relative: str) -> None:
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError as error:
        raise StagePackageError(f"{relative!r}: name is not UTF-8") from error


def read_stage_tree(root: Path) -> tuple[StageFile, ...]:
    """The shipped files of the package at ``root``, sorted by path; refuses unsafe entries."""
    if root.is_symlink() or not root.is_dir():
        raise StagePackageError(f"{root}: a package is a real directory")
    return tuple(sorted(_walk(root), key=attrgetter("path")))


def stage_tree_digest(files: tuple[StageFile, ...]) -> str:
    """The ``stage-tree/1`` digest of ``files`` (as ``read_stage_tree`` returns them)."""
    rows = [
        [item.path, item.sha256, item.executable] for item in sorted(files, key=attrgetter("path"))
    ]
    return digest_json({"scheme": TREE_SCHEME, "files": rows})
