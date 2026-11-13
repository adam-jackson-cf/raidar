"""The filesystem seam for analyzer-like code.

Scoring, ``evaluation.analysis`` and ``engine.health`` read and write files only through these
functions (rule PYS251), so the effect is owned here, in the lowest tier every one of them may
import. Each function is the plain ``pathlib`` call it names: same arguments, same result, same
errors. The module imports only the standard library, so an isolated tool interpreter can load
it from its file and pass it to a program as that program's reader.
"""

from __future__ import annotations

import os
from importlib.resources.abc import Traversable
from pathlib import Path


def load_text(path: Path, *, encoding: str | None = None, errors: str | None = None) -> str:
    """``path.read_text(encoding, errors)``."""
    return path.read_text(encoding=encoding, errors=errors)


def load_bytes(path: Path) -> bytes:
    """``path.read_bytes()``."""
    return path.read_bytes()


def load_resource_text(resource: Traversable, *, encoding: str | None = None) -> str:
    """The text of a packaged resource (``importlib.resources``)."""
    return resource.read_text(encoding=encoding)


def store_text(path: Path, text: str, *, encoding: str | None = None) -> None:
    """``path.write_text(text, encoding)``."""
    path.write_text(text, encoding=encoding)


def store_bytes(path: Path, data: bytes) -> None:
    """``path.write_bytes(data)``."""
    path.write_bytes(data)


def append_private_line(path: Path, line: str) -> None:
    """Append ``line`` and a newline to an owner-only file (0600, parent 0700), never via a link."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (line + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
