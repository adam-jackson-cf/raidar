"""Grading sandbox: where hidden tests run against a copy of the snapshot.

The sandbox is a fresh grader container (``DockerCopySandbox``) with
``--network none``, a non-root user, all capabilities dropped, and a tar copy
of the snapshot as ``/app`` plus the read-only hidden tests, copied in with
``docker cp``: no host path is mounted, so the Docker daemon may be on another
host. Nothing is ever uploaded to, or executed in, the agent's environment.

The pytest invocation is rendered against a ``SandboxLayout``. The rendered
argument list (without the interpreter and the coverage wrapper) is the
invocation identity recorded as parity evidence.

The grader interpreter always runs with ``-P`` (safe path): the working
directory is the submission copy, and without ``-P`` Python would put it first
on ``sys.path``, so a submission file named ``pytest.py``, ``coverage.py`` or a
stdlib module would replace the grader itself (and could forge the ledger).
``-P`` is a flag, not ``PYTHONSAFEPATH``, so it does not leak into the
submission processes the black-box tests start.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from raidar.grading.bundle import (
    APP_PLACEHOLDER,
    TESTS_PLACEHOLDER,
    VerifierBundle,
    VerifierStepSpec,
)

RUNTIME_DIR = Path(__file__).resolve().parent / "runtime"
PLUGIN_MODULE = "raidar_oracle_plugin"
PLUGIN_FILE = RUNTIME_DIR / f"{PLUGIN_MODULE}.py"
LEDGER_NAME = "ledger.json"
SANDBOX_USER = "1000:1000"
PIDS_LIMIT = "1024"
KILL_TIMEOUT_S = 30.0
TRANSFER_TIMEOUT_S = 600.0
SWEEP_TIMEOUT_S = 120.0
ORPHAN_AGE = "1h"
GRADER_ROLE_LABEL = "raidar.role=grader"
HOST_LABEL = "raidar.host"
SCOPE_LABEL = "raidar.run_scope"
COPY_TIMINGS_PREFIX = "[raidar-copy]"
SAFE_PATH_FLAG = "-P"


@dataclass(frozen=True)
class SandboxLayout:
    app: str
    tests: str
    runtime: str
    out: str
    scratch: str
    python: str


CONTAINER_LAYOUT = SandboxLayout(
    app="/app",
    tests="/tests",
    runtime="/oracle/runtime",
    out="/oracle/out",
    scratch="/tmp/raidar-oracle",
    python="/opt/raidar-grader/bin/python",
)


@dataclass(frozen=True)
class SandboxRun:
    app_dir: Path
    tests_dir: Path
    out_dir: Path
    argv: tuple[str, ...]
    env: Mapping[str, str]
    timeout_s: float
    cpus: float
    memory_mb: int


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None
    timed_out: bool
    output: str


class GradingSandbox(Protocol):
    @property
    def layout(self) -> SandboxLayout: ...

    async def environment_id(self) -> str: ...

    async def run(self, run: SandboxRun) -> SandboxResult: ...


def render_pytest_args(step: VerifierStepSpec, layout: SandboxLayout) -> tuple[str, ...]:
    """Canonical pytest arguments: Raidar's fixed isolation flags + the step's own."""
    fixed = (
        "-c",
        f"{layout.runtime}/pytest.ini",
        f"--rootdir={layout.tests}",
        f"--confcutdir={layout.tests}",
        "-p",
        "no:cacheprovider",
        "-p",
        PLUGIN_MODULE,
        f"--basetemp={layout.scratch}/pytest-tmp",
    )
    rendered = tuple(
        arg.replace(TESTS_PLACEHOLDER, layout.tests).replace(APP_PLACEHOLDER, layout.app)
        for arg in step.pytest_args
    )
    return fixed + rendered


def coverage_prefix(layout: SandboxLayout) -> tuple[str, ...]:
    """Measurement-only wrapper for the separate coverage pass."""
    return (
        "-m",
        "coverage",
        "run",
        f"--rcfile={layout.runtime}/coverage.rc",
        f"--source={layout.app}",
    )


def sandbox_argv(
    step: VerifierStepSpec, layout: SandboxLayout, *, coverage: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (full argv, measurement prefix)."""
    prefix = coverage_prefix(layout) if coverage else ()
    argv = (
        layout.python,
        SAFE_PATH_FLAG,
        *prefix,
        "-m",
        "pytest",
        *render_pytest_args(step, layout),
    )
    return argv, prefix


def sandbox_env(
    bundle: VerifierBundle,
    layout: SandboxLayout,
    *,
    coverage: bool,
    budget_s: float | None = None,
    python_path: tuple[str, ...] = (),
) -> dict[str, str]:
    """The grader's environment; ``python_path`` adds the submission's provided packages."""
    env = dict(bundle.manifest.env)
    env.update(
        {
            "PYTHONPATH": ":".join((layout.runtime, *python_path)),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": f"{layout.scratch}/home",
            "UV_CACHE_DIR": f"{layout.scratch}/uv-cache",
            "UV_OFFLINE": "1",
            # Entrypoints are `uv run --project {app} ...`. Native SCB grades with
            # network, so uv can sync whatever the submission's pyproject declares
            # (e.g. a `dev = ["pytest"]` group). Offline that sync fails before the
            # program starts and every case reads as failed. Running unsynced keeps
            # the graded program the submission's own code, not its dev tooling.
            "UV_NO_SYNC": "1",
            "COVERAGE_FILE": f"{layout.scratch}/coverage-data/coverage",
            "RAIDAR_ORACLE_LEDGER": f"{layout.out}/{LEDGER_NAME}",
            "RAIDAR_ORACLE_SUBMISSION_ROOT": layout.app,
            "RAIDAR_ORACLE_SCRATCH": layout.scratch,
            "RAIDAR_ORACLE_RUNTIME": layout.runtime,
            "RAIDAR_ORACLE_COVERAGE": "1" if coverage else "0",
        }
    )
    if budget_s is not None:
        env["RAIDAR_ORACLE_BUDGET_S"] = f"{budget_s:g}"
    return env


@dataclass(frozen=True)
class DockerCopySandbox:
    """Fresh, no-network grader container per run, fed and read back by ``docker cp``.

    No host path is mounted, so the daemon may be on another host. The container
    is created with the lockdown flags and Raidar labels, receives one tar
    (``/app`` owned by the sandbox user; tests and runtime root-owned and
    read-only), runs under ``start -a`` with a timeout, and its out directory is
    copied back. The container is always force-removed afterwards. Timings of the
    three phases are appended to the output as a ``[raidar-copy]`` line.
    """

    image: str
    docker_context: str
    docker_bin: str = "docker"
    run_scope: str = ""

    @property
    def layout(self) -> SandboxLayout:
        return CONTAINER_LAYOUT

    def _docker(self, *args: str) -> list[str]:
        return [self.docker_bin, "--context", self.docker_context, *args]

    def create_argv(self, run: SandboxRun, name: str) -> list[str]:
        labels = [GRADER_ROLE_LABEL, f"{HOST_LABEL}={socket.gethostname()}"]
        if self.run_scope:
            labels.append(f"{SCOPE_LABEL}={self.run_scope}")
        argv = self._docker(
            "create",
            "--name",
            name,
            "--network",
            "none",
            "--user",
            SANDBOX_USER,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            PIDS_LIMIT,
            "--cpus",
            str(run.cpus),
            "--memory",
            f"{run.memory_mb}m",
        )
        for label in labels:
            argv += ["--label", label]
        argv += ["-w", self.layout.app]
        for key, value in sorted(run.env.items()):
            argv += ["-e", f"{key}={value}"]
        return [*argv, self.image, *run.argv]

    async def environment_id(self) -> str:
        code, output = await self._exec(
            self._docker("image", "inspect", "--format", "{{.Id}}", self.image)
        )
        text = output.decode(errors="replace").strip()
        if code != 0 or not text:
            raise RuntimeError(f"grader image {self.image} is not available: {text}")
        return text

    async def _exec(
        self, argv: list[str], *, stdin: IO[bytes] | None = None, stdout: IO[bytes] | None = None
    ) -> tuple[int | None, bytes]:
        """Run a docker command to completion; (exit code, stdout+stderr or just stderr)."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=stdout if stdout is not None else asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if stdout is None else asyncio.subprocess.PIPE,
        )
        try:
            data, err = await asyncio.wait_for(proc.communicate(), timeout=TRANSFER_TIMEOUT_S)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return None, f"{argv[3]} timed out after {TRANSFER_TIMEOUT_S:.0f} s".encode()
        return proc.returncode, (data if stdout is None else err) or b""

    async def run(self, run: SandboxRun) -> SandboxResult:
        name = f"raidar-oracle-{uuid.uuid4().hex[:12]}"
        try:
            return await self._run(run, name)
        finally:
            with contextlib.suppress(Exception):  # the orphan sweep catches what this misses
                await self._exec(self._docker("rm", "-f", name))

    async def _run(self, run: SandboxRun, name: str) -> SandboxResult:
        layout = self.layout
        timings: dict[str, float] = {}
        with tempfile.TemporaryDirectory(prefix="raidar-copy-") as tmp:
            archive, back = Path(tmp) / "in.tar", Path(tmp) / "out.tar"
            code, output = await self._exec(self.create_argv(run, name))
            if code != 0:
                return SandboxResult(code, False, output.decode(errors="replace"))
            mark = time.perf_counter()
            await asyncio.to_thread(build_copy_in_tar, run, layout, archive)
            with archive.open("rb") as source:
                code, output = await self._exec(self._docker("cp", "-", f"{name}:/"), stdin=source)
            timings["copy_in_s"] = time.perf_counter() - mark
            if code != 0:
                return SandboxResult(code, False, output.decode(errors="replace"))

            proc, stdout, timed_out = await self._start_and_wait(run, name, timings)
            failure = await self._copy_out(name, back, run.out_dir, timings)
        text = stdout.decode(errors="replace")
        if failure:
            text += f"\n{COPY_TIMINGS_PREFIX} copy-out failed: {failure}"
        text += f"\n{COPY_TIMINGS_PREFIX} " + " ".join(f"{k}={v:.3f}" for k, v in timings.items())
        return SandboxResult(None if timed_out else proc.returncode, timed_out, text)

    async def _start_and_wait(
        self, run: SandboxRun, name: str, timings: dict[str, float]
    ) -> tuple[asyncio.subprocess.Process, bytes, bool]:
        """Start the grader container and wait for it; (process, output, timed out)."""
        mark = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *self._docker("start", "-a", name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        timed_out = False
        try:
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=run.timeout_s)
            except TimeoutError:
                timed_out = True
                try:
                    await self._kill(name)
                finally:
                    proc.kill()
                stdout, _ = await proc.communicate()
        finally:
            if proc.returncode is None:  # failed kill or cancelled run: reap `start -a`
                proc.kill()
        timings["run_s"] = time.perf_counter() - mark
        return proc, stdout, timed_out

    async def _copy_out(
        self, name: str, back: Path, out_dir: Path, timings: dict[str, float]
    ) -> str:
        """Copy the out directory back and unpack it; why that failed, or ``""``."""
        mark = time.perf_counter()
        with back.open("wb") as sink:
            copied, err = await self._exec(
                self._docker("cp", f"{name}:{self.layout.out}", "-"), stdout=sink
            )
        failure = err.decode(errors="replace") if copied != 0 else ""
        if copied == 0:
            try:
                await asyncio.to_thread(extract_out_tar, back, out_dir)
            except (tarfile.TarError, OSError) as error:
                failure = f"unpacking the out directory failed: {error}"
        timings["copy_out_s"] = time.perf_counter() - mark
        return failure

    async def _kill(self, name: str) -> None:
        await asyncio.wait_for(self._exec(self._docker("kill", name)), timeout=KILL_TIMEOUT_S)


def sweep_grader_orphans(docker_context: str, docker_bin: str = "docker") -> None:
    """Remove stopped Raidar grader containers a crashed run left on ``docker_context``.

    Only containers labelled ``raidar.role=grader`` and older than ``ORPHAN_AGE``
    are removed, so a concurrent job's grader between ``start`` and copy-out is safe.
    Best effort: a failure here never blocks the work it precedes.
    """
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            [
                docker_bin,
                "--context",
                docker_context,
                "container",
                "prune",
                "--force",
                "--filter",
                f"label={GRADER_ROLE_LABEL}",
                "--filter",
                f"until={ORPHAN_AGE}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=SWEEP_TIMEOUT_S,
            check=False,
        )


def _stamp(
    owner: tuple[int, int], *, writable: bool
) -> Callable[[tarfile.TarInfo], tarfile.TarInfo]:
    """tarfile filter: fixed ownership; owner-writable, or readable with no write bits."""

    def apply(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid, info.gid, info.uname, info.gname = owner[0], owner[1], "", ""
        if info.issym():
            return info
        if writable:
            info.mode |= stat.S_IRWXU if info.isdir() else stat.S_IRUSR | stat.S_IWUSR
        else:
            info.mode &= ~(stat.S_IWGRP | stat.S_IWOTH | stat.S_IWUSR)
            info.mode |= 0o555 if info.isdir() else 0o444
        return info

    return apply


def _add_tree(
    bundle: tarfile.TarFile, source: Path, arcname: str, owner: tuple[int, int], *, writable: bool
) -> None:
    bundle.add(source, arcname=arcname, recursive=True, filter=_stamp(owner, writable=writable))


def _add_dir(bundle: tarfile.TarFile, arcname: str, owner: tuple[int, int], mode: int) -> None:
    info = tarfile.TarInfo(arcname)
    info.type = tarfile.DIRTYPE
    info.mode, info.uid, info.gid = mode, owner[0], owner[1]
    info.mtime = int(time.time())
    bundle.addfile(info)


def build_copy_in_tar(run: SandboxRun, layout: SandboxLayout, archive: Path) -> None:
    """The grader's whole filesystem delta as one tar rooted at ``/``.

    ``/app`` belongs to the sandbox user and stays writable; tests and runtime are
    root-owned without write bits; the out directory is empty and owned by the
    sandbox user. Symlinks are stored as symlinks.
    """
    uid, gid = (int(part) for part in SANDBOX_USER.split(":"))
    owner, root = (uid, gid), (0, 0)
    with tarfile.open(archive, "w", format=tarfile.GNU_FORMAT) as bundle:
        _add_tree(bundle, run.app_dir, layout.app.lstrip("/"), owner, writable=True)
        _add_tree(bundle, run.tests_dir, layout.tests.lstrip("/"), root, writable=False)
        parent = layout.runtime.lstrip("/").rsplit("/", 1)[0]
        _add_dir(bundle, parent, root, 0o755)
        _add_tree(bundle, RUNTIME_DIR, layout.runtime.lstrip("/"), root, writable=False)
        _add_dir(bundle, layout.out.lstrip("/"), owner, 0o755)


def extract_out_tar(archive: Path, out_dir: Path) -> None:
    """Unpack ``docker cp <ctr>:<out> -`` (members prefixed ``out/``) into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:") as bundle:
        members = []
        for member in bundle.getmembers():
            _, _, rest = member.name.partition("/")
            if not rest:
                continue
            member.name = rest
            members.append(member)
        bundle.extractall(out_dir, members=members, filter="data")
