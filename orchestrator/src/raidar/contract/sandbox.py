"""What Raidar needs of a Harbor environment: commands (``Shell``) and file transfers.

Harbor's ``BaseEnvironment`` satisfies both structurally; Raidar code and its test doubles
depend on these interfaces, never on Harbor's class.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from harbor.environments.base import ExecResult


class Shell(Protocol):
    """A container Raidar runs one command in."""

    async def exec(
        self,
        command: str,
        /,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult: ...


class Sandbox(Shell, Protocol):
    """A container Raidar runs commands in and copies files to and from."""

    async def upload_file(self, source: Path, target: str, /) -> None: ...

    async def upload_dir(self, source: Path, target: str, /) -> None: ...

    async def download_file(self, source: str, target: Path, /) -> None: ...

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path, exclude: list[str]
    ) -> None: ...
