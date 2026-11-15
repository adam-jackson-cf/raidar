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
from harbor.environments.base import ExecResult

from raidar.subject.primitive import calls, install

UNCONFINED = calls.ChildPolicy(uid=None, gid=None, seccomp=False, owner=None)


class NoTransfers:
    """The ``Sandbox`` file transfers a scenario never makes: reaching one fails the test.

    A test double that stands in for a container defines the transfers its scenario makes
    and inherits these for the rest, so it still implements the whole owned interface.
    The signatures are Harbor's own, so a double may also extend a Harbor environment.
    """

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        raise AssertionError(f"this scenario never uploads the file {source_path}")

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        raise AssertionError(f"this scenario never uploads the directory {source_dir}")

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        raise AssertionError(f"this scenario never downloads the file {source_path}")

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path | str, exclude: list[str]
    ) -> None:
        raise AssertionError(f"this scenario never downloads the directory {source_dir}")


class UntouchedSandbox(NoTransfers):
    """A container a scenario hands over but never reaches (its seams are patched)."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        raise AssertionError(f"this scenario never runs a command: {command}")


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

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path, exclude: list[str]
    ) -> None:
        ignore = shutil.ignore_patterns(*(pattern.strip("/") for pattern in exclude))
        shutil.copytree(source_dir, target_dir, ignore=ignore, dirs_exist_ok=True)


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
