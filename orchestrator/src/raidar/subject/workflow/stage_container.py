"""A throwaway, offline pack-image container for ``stage-check`` (public contract: stage/1).

Package code never runs on the host: fixtures and replays run in a container created from the
pack image with no network. It has the environment surface the call primitives use (``exec``,
``upload_*``, ``download_*``) over the Docker CLI of one Docker context, and is always removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from harbor.environments.base import ExecResult

ROLE_LABEL = "raidar.role=stage-check"
LIFETIME_S = 3600
DOCKER_TIMEOUT_S = 300.0


class ContainerError(RuntimeError):
    """Docker could not create, copy to or remove the container."""


class PackContainer:
    """One offline container of ``image`` on ``docker_context``; use as ``async with``."""

    def __init__(self, image: str, docker_context: str, docker_bin: str = "docker") -> None:
        self.image = image
        self.docker_context = docker_context
        self.docker_bin = docker_bin
        self.name = f"raidar-stage-check-{uuid.uuid4().hex[:12]}"

    def _docker(self, *args: str) -> list[str]:
        return [self.docker_bin, "--context", self.docker_context, *args]

    async def _run(
        self, argv: Sequence[str], timeout: float | None = DOCKER_TIMEOUT_S
    ) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return 124, "", f"{argv[3]} timed out after {timeout}s"
        return (
            process.returncode if process.returncode is not None else 1,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
        )

    async def _must(self, *args: str) -> None:
        code, _, err = await self._run(self._docker(*args))
        if code != 0:
            raise ContainerError(f"docker {args[0]} failed ({code}): {err.strip()[-500:]}")

    async def __aenter__(self) -> PackContainer:
        await self._must(
            "run",
            "--detach",
            "--name",
            self.name,
            "--network",
            "none",
            "--label",
            ROLE_LABEL,
            "--entrypoint",
            "sleep",
            self.image,
            str(LIFETIME_S),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        with contextlib.suppress(Exception):
            await asyncio.shield(self._run(self._docker("rm", "--force", self.name), 60))

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Run ``command`` through ``bash -c``; a failure to exec raises ``ContainerError``."""
        argv = self._docker("exec")
        if user is not None:
            argv += ["--user", str(user)]
        if cwd:
            argv += ["--workdir", cwd]
        for key, value in sorted((env or {}).items()):
            argv += ["--env", f"{key}={value}"]
        argv += [self.name, "bash", "-c", command]
        code, out, err = await self._run(argv, None if timeout_sec is None else timeout_sec)
        return ExecResult(stdout=out, stderr=err, return_code=code)

    async def upload_file(self, source: Path, target: str) -> None:
        await self._must("cp", str(source), f"{self.name}:{target}")

    async def upload_dir(self, source: Path, target: str) -> None:
        await self._must("cp", f"{source}/.", f"{self.name}:{target}")

    async def download_file(self, source: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        await self._must("cp", f"{self.name}:{source}", str(target))

    async def download_dir(self, source: str, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        await self._must("cp", f"{self.name}:{source}/.", str(target))

    async def download_dir_with_exclusions(
        self, source_dir: str, target_dir: Path, exclude: Sequence[str]
    ) -> None:
        """Copy a directory and drop excluded names host-side (names, at any depth)."""
        with tempfile.TemporaryDirectory(prefix="raidar-stage-check-") as scratch:
            await self.download_dir(source_dir, Path(scratch))
            for pattern in exclude:
                for found in sorted(Path(scratch).rglob(pattern.strip("/"))):
                    if found.is_dir() and not found.is_symlink():
                        shutil.rmtree(found, ignore_errors=True)
                    else:
                        found.unlink(missing_ok=True)
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(scratch, target_dir, dirs_exist_ok=True, symlinks=True)
