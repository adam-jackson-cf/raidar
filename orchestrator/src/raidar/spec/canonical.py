"""Canonical JSON serialisation and content hashing for spec artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_CHUNK_BYTES = 1 << 20

# Names never hashed into a lock and never copied into an agent workspace: the
# lock hashes exactly the bytes components ship (``components.builtins._common``).
IGNORED_TREE_NAMES = frozenset({".DS_Store", "__pycache__", ".git"})


def canonical_json_bytes(value: object) -> bytes:
    """Compact, key-sorted UTF-8 JSON used for every digest."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def pretty_canonical_json_bytes(value: object) -> bytes:
    """Key-sorted, indented UTF-8 JSON with a trailing newline (lock files)."""
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_json(value: object) -> str:
    return sha256_hex(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
