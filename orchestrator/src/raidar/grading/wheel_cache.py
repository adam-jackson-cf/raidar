"""Host wheel cache: the wheels a lock lists, fetched once, hash-checked, kept under ``var/``.

Acquisition downloads files and checks their sha256 against the lock; nothing is
unpacked, built or imported, so no package code runs. A cache hit needs no
network, so regrading a recorded lock works with the index unreachable; a miss
with the index unreachable is ``DependencyInfraError`` (an infrastructure
failure, never a score).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO, cast

from raidar.contract.paths import var_root
from raidar.grading.resolver import (
    MAX_WHEEL_BYTES,
    WHEEL_URL_PREFIXES,
    DependencyInfraError,
    lock_wheels,
)

ALLOWED_SCHEMES = ("https", "file")
DOWNLOAD_TIMEOUT_S = 120.0
_CHUNK = 1 << 20


def default_cache_root() -> Path:
    return var_root() / "cache" / "wheels"


def open_wheel(url: str) -> BinaryIO:
    """Open one wheel URL for reading. The single network seam of wheel acquisition."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"wheel url {url!r} is not one of {ALLOWED_SCHEMES}")
    return cast(BinaryIO, urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S))  # noqa: S310


def _filename(url: str) -> str:
    return urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)


def _download(url: str, expected: str, target: Path) -> None:
    digest = hashlib.sha256()
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".part") as partial:
        temporary = Path(partial.name)
        try:
            with open_wheel(url) as source:
                while chunk := source.read(_CHUNK):
                    total += len(chunk)
                    if total > MAX_WHEEL_BYTES:
                        raise ValueError(f"wheel {url} exceeds the closure byte cap")
                    digest.update(chunk)
                    partial.write(chunk)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            temporary.unlink(missing_ok=True)
            raise DependencyInfraError(f"could not fetch {url}: {error}") from error
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    if digest.hexdigest() != expected:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"wheel {url} does not match its locked sha256 {expected[:12]}")
    os.replace(temporary, target)


def fetch_wheels(lock_text: str, destination: Path, *, cache_root: Path | None = None) -> int:
    """Put every wheel the lock lists into ``destination``; the number of files placed."""
    root = cache_root or default_cache_root()
    destination.mkdir(parents=True, exist_ok=True)
    placed = 0
    for wheel in lock_wheels(lock_text):
        url = str(wheel["url"])
        if not url.startswith(tuple(WHEEL_URL_PREFIXES)):
            raise ValueError(f"wheel url {url!r} is not under an allowed host {WHEEL_URL_PREFIXES}")
        hashes = wheel.get("hashes")
        if not isinstance(hashes, dict) or "sha256" not in hashes:
            raise ValueError(f"lock lists wheel {url} without a sha256")
        expected = str(hashes["sha256"])
        cached = root / expected / _filename(url)
        if not cached.is_file():
            _download(url, expected, cached)
        shutil.copyfile(cached, destination / cached.name)
        placed += 1
    return placed
