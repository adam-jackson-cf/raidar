"""A local stand-in for a Harbor environment: commands and copies run on the test host.

Only the ``exec``/upload/download transport is faked; the trusted payload, the supervisor and
every command run for real. The paths ``calls.py`` roots in ``/opt`` and ``/var`` are moved
under a temporary directory and the child is not dropped to UID 1000 (a test host is not
root and may not be Linux); ``ChildPolicy`` is the one explicit switch for that.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raidar.subject.primitive import calls, install

UNCONFINED = calls.ChildPolicy(uid=None, gid=None, seccomp=False, owner=None)


class LocalEnvironment:
    """``exec`` through ``bash -c`` and uploads/downloads as file copies."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Any:
        self.commands.append(command)
        done = subprocess.run(  # noqa: S603 - a test double running Raidar's own commands
            ["bash", "-c", command],  # noqa: S607
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout_sec,
            check=False,
        )
        return SimpleNamespace(stdout=done.stdout, stderr=done.stderr, return_code=done.returncode)

    async def upload_file(self, source: Path | str, target: str) -> None:
        shutil.copyfile(source, target)

    async def upload_dir(self, source: Path | str, target: str) -> None:
        shutil.copytree(source, target, dirs_exist_ok=True)

    async def download_file(self, source: str, target: Path | str) -> None:
        shutil.copyfile(source, target)

    async def download_dir(self, source: str, target: Path | str) -> None:
        shutil.copytree(source, target, dirs_exist_ok=True)


@dataclass(frozen=True)
class Tree:
    """A stand-in installed tool tree for ``CallRunner.run``."""

    reference: str
    root: str
    digest: str
    bin: str = "/nonexistent/bin"


def relocate(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Root the container paths under ``root`` and use this interpreter as the pack's."""
    monkeypatch.setattr(calls, "TRUSTED_ROOT", str(root / "opt" / "trusted"))
    monkeypatch.setattr(calls, "STAGES_ROOT", str(root / "opt" / "stages"))
    monkeypatch.setattr(calls, "CALLS_ROOT", str(root / "var" / "calls"))
    monkeypatch.setattr(calls, "TRUSTED_PYTHON", sys.executable)
    links = root / "usr" / "bin"
    links.mkdir(parents=True)
    monkeypatch.setattr(install, "LINKS_DIR", str(links))


def lay(root: Path) -> Path:
    """A small tree: ``a.txt`` and ``d/b.bin``."""
    (root / "d").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"alpha")
    (root / "d" / "b.bin").write_bytes(b"beta!")
    return root


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
