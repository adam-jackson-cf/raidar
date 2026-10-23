"""Grading sandbox: where hidden tests run against a copy of the snapshot.

The canonical sandbox is a fresh ``docker run --rm`` of the grader image with
``--network none``, a non-root user, all capabilities dropped, the hidden tests
mounted read-only, and a throwaway copy of the snapshot as ``/app``. Nothing is
ever uploaded to, or executed in, the agent's environment.

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
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from raidar.verifier.bundle import (
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


def docker_cli_env() -> dict[str, str]:
    """Host env for the docker CLI; an explicit --context must not fight DOCKER_HOST."""
    return {k: v for k, v in os.environ.items() if k not in {"DOCKER_HOST", "DOCKER_CONTEXT"}}


@dataclass(frozen=True)
class DockerSandbox:
    """Fresh, no-network grader container per run on an explicit Docker context."""

    image: str
    docker_context: str
    docker_bin: str = "docker"

    @property
    def layout(self) -> SandboxLayout:
        return CONTAINER_LAYOUT

    def _docker(self, *args: str) -> list[str]:
        return [self.docker_bin, "--context", self.docker_context, *args]

    def run_argv(self, run: SandboxRun, name: str) -> list[str]:
        layout = self.layout
        argv = self._docker(
            "run",
            "--rm",
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
            "-v",
            f"{run.app_dir}:{layout.app}",
            "-v",
            f"{run.tests_dir}:{layout.tests}:ro",
            "-v",
            f"{RUNTIME_DIR}:{layout.runtime}:ro",
            "-v",
            f"{run.out_dir}:{layout.out}",
            "-w",
            layout.app,
        )
        for key, value in sorted(run.env.items()):
            argv += ["-e", f"{key}={value}"]
        return [*argv, self.image, *run.argv]

    async def environment_id(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            *self._docker("image", "inspect", "--format", "{{.Id}}", self.image),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=docker_cli_env(),
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode(errors="replace").strip()
        if proc.returncode != 0 or not text:
            raise RuntimeError(f"grader image {self.image} is not available: {text}")
        return text

    async def run(self, run: SandboxRun) -> SandboxResult:
        name = f"raidar-oracle-{uuid.uuid4().hex[:12]}"
        proc = await asyncio.create_subprocess_exec(
            *self.run_argv(run, name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=docker_cli_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=run.timeout_s)
        except TimeoutError:
            await self._kill(name)
            proc.kill()
            stdout, _ = await proc.communicate()
            return SandboxResult(None, True, stdout.decode(errors="replace"))
        return SandboxResult(proc.returncode, False, stdout.decode(errors="replace"))

    async def _kill(self, name: str) -> None:
        killer = await asyncio.create_subprocess_exec(
            *self._docker("kill", name),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=docker_cli_env(),
        )
        await asyncio.wait_for(killer.wait(), timeout=KILL_TIMEOUT_S)
