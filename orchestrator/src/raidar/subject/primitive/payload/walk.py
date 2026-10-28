"""Bounded, no-follow tree manifest and snapshot of a directory (or one file).

Never follows a link and never opens a FIFO, device or socket: every entry is ``lstat``-ed
first and regular files are opened with ``O_NOFOLLOW | O_NONBLOCK`` and re-checked with
``fstat``. Violations stop the walk and are reported, never raised, so the caller can refuse
before any authored code reads the tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

_CHUNK = 1 << 16
Entry = dict[str, Any]


@dataclass(frozen=True)
class Limits:
    """Walk bounds; ``None`` is unbounded (trusted installs)."""

    max_bytes: int | None = None
    max_files: int | None = None
    max_depth: int | None = None
    links: bool = False
    owner: int | None = None

    @classmethod
    def from_spec(cls, spec: dict[str, Any] | None) -> Limits:
        return cls(**spec) if spec else cls()


class Violation(Exception):  # noqa: N818 - control flow: the walk ends with this reason
    """The tree breaks a bound or contains something that must not be read."""


def tree_digest(entries: list[Entry]) -> str:
    text = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _entry(rel: str, kind: str, info: os.stat_result, size: int = 0, sha256: str = "") -> Entry:
    return {
        "path": rel,
        "type": kind,
        "mode": stat.S_IMODE(info.st_mode),
        "size": size,
        "sha256": sha256,
    }


def _open_regular(path: str, before: os.stat_result) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    after = os.fstat(fd)
    same = (after.st_ino, after.st_dev) == (before.st_ino, before.st_dev)
    if not stat.S_ISREG(after.st_mode) or not same:
        os.close(fd)
        raise Violation("changed while read")
    return fd


def _hash_file(path: str, before: os.stat_result) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(_open_regular(path, before), "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class _Walk:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.entries: list[Entry] = []
        self.files = 0
        self.bytes = 0

    def _guard(self, rel: str, info: os.stat_result, depth: int) -> None:
        limits = self.limits
        if limits.max_depth is not None and depth > limits.max_depth:
            raise Violation(f"too deep (> {limits.max_depth}): {rel}")
        if limits.owner is not None and info.st_uid != limits.owner:
            raise Violation(f"owner: {rel} is owned by uid {info.st_uid}")

    def _count(self, rel: str, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.limits.max_files is not None and self.files > self.limits.max_files:
            raise Violation(f"too many files (> {self.limits.max_files})")
        if self.limits.max_bytes is not None and self.bytes > self.limits.max_bytes:
            raise Violation(f"too many bytes (> {self.limits.max_bytes}) at {rel}")

    def _file(self, path: str, rel: str, info: os.stat_result) -> None:
        try:
            sha256, size = _hash_file(path, info)
        except OSError as error:
            raise Violation(f"unreadable: {rel}: {error.strerror}") from error
        self._count(rel, size)
        self.entries.append(_entry(rel, "file", info, size, sha256))

    def _link(self, path: str, rel: str, info: os.stat_result) -> None:
        if not self.limits.links:
            raise Violation(f"symlink: {rel}")
        self.entries.append({**_entry(rel, "link", info), "mode": 0, "target": os.readlink(path)})

    def visit(self, path: str, rel: str, depth: int) -> None:
        info = os.lstat(path)
        self._guard(rel, info, depth)
        mode = info.st_mode
        if stat.S_ISDIR(mode):
            self._directory(path, rel, depth, info)
        elif stat.S_ISREG(mode):
            self._file(path, rel, info)
        elif stat.S_ISLNK(mode):
            self._link(path, rel, info)
        else:
            raise Violation(f"special file: {rel}")

    def _directory(self, path: str, rel: str, depth: int, info: os.stat_result) -> None:
        if rel:
            self.entries.append(_entry(rel, "dir", info))
        for name in sorted(os.listdir(path)):
            self.visit(os.path.join(path, name), f"{rel}/{name}" if rel else name, depth + 1)


def scan(root: str, limits: Limits, *, entries: bool = True) -> dict[str, Any]:
    """The manifest of ``root``: ``entries``, ``digest``, ``files``, ``bytes``, ``violation``."""
    walk = _Walk(limits)
    single = os.path.lexists(root) and not stat.S_ISDIR(os.lstat(root).st_mode)
    result: dict[str, Any] = {"path": root, "exists": True, "violation": None, "single": single}
    try:
        walk.visit(root, os.path.basename(root) if single else "", 0)
    except FileNotFoundError:
        result.update(exists=False, violation="missing")
    except Violation as stop:
        result["violation"] = str(stop)
    except OSError as error:
        result["violation"] = f"unreadable: {error.strerror}"
    result.update(files=walk.files, bytes=walk.bytes, digest=tree_digest(walk.entries))
    result["entries"] = walk.entries if entries else []
    return result


class _Exact:
    """A file object that yields exactly ``size`` bytes or fails (the file changed)."""

    def __init__(self, handle: BinaryIO, size: int) -> None:
        self.handle = handle
        self.left = size

    def read(self, count: int = -1) -> bytes:
        want = self.left if count < 0 else min(count, self.left)
        data = self.handle.read(want)
        if want and not data:
            raise Violation("changed while archived")
        self.left -= len(data)
        return data


def _add_file(archive: tarfile.TarFile, path: str, entry: Entry) -> None:
    fd = _open_regular(path, os.lstat(path))
    member = tarfile.TarInfo(entry["path"])
    member.size = entry["size"]
    member.mode = entry["mode"]
    with os.fdopen(fd, "rb") as handle:
        archive.addfile(member, _Exact(handle, entry["size"]))  # type: ignore[arg-type]


def snapshot(root: str, tar_path: str, limits: Limits) -> dict[str, Any]:
    """``scan`` ``root``; when clean, archive its regular files into ``tar_path`` (0600)."""
    result = scan(root, limits)
    if result["violation"] is not None:
        return result
    fd = os.open(tar_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as out, tarfile.open(fileobj=out, mode="w") as archive:
            for entry in result["entries"]:
                if entry["type"] == "file":
                    path = root if result["single"] else os.path.join(root, entry["path"])
                    _add_file(archive, path, entry)
    except (Violation, OSError) as error:
        Path(tar_path).unlink(missing_ok=True)
        result["violation"] = f"snapshot failed: {error}"
    return result
