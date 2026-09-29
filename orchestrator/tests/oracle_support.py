"""Builders and fakes for oracle plugin tests.

``LocalProcessSandbox`` runs the real grader invocation (real pytest, the real
in-sandbox plugin, real coverage.py) as a host subprocess instead of a Docker
container, so grading, normalisation and parity behaviour are exercised without
Docker. Container isolation itself is covered by argv assertions and by the
recorded forge validation.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.oracle.bundle import BundleManifest, GraderSpec, OracleStepSpec, write_bundle
from raidar.oracle.sandbox import RUNTIME_DIR, SandboxLayout, SandboxResult, SandboxRun

CONFTEST = """
import shlex

import pytest


def pytest_addoption(parser):
    parser.addoption("--entrypoint", action="store", required=True)
    parser.addoption("--checkpoint", action="store", required=True)


def pytest_configure(config):
    for name in ("error", "functionality", "regression"):
        config.addinivalue_line("markers", f"{name}: scb group")


@pytest.fixture(scope="session")
def entrypoint_argv(request):
    return shlex.split(request.config.getoption("--entrypoint"))
"""

TEST_CHECKPOINT_1 = """
import subprocess

import pytest


def run(argv, *args):
    return subprocess.run([*argv, *args], capture_output=True, text=True, check=False)


def test_adds(entrypoint_argv):
    assert run(entrypoint_argv, "add", "2", "3").stdout.strip() == "5"


@pytest.mark.functionality
def test_adds_negative(entrypoint_argv):
    assert run(entrypoint_argv, "add", "-2", "3").stdout.strip() == "1"


@pytest.mark.error
def test_rejects_non_numbers(entrypoint_argv):
    assert run(entrypoint_argv, "add", "x", "1").returncode == 2


@pytest.mark.skip(reason="fixture skip")
def test_skipped():
    raise AssertionError("never runs")
"""

TEST_CHECKPOINT_2 = """
import subprocess


def test_multiplies(entrypoint_argv):
    result = subprocess.run([*entrypoint_argv, "mul", "2", "3"], capture_output=True, text=True)
    assert result.stdout.strip() == "6"
"""

APP_HEADER = """
import os
import sys


def main(argv):
    op, *raw = argv
    try:
        a, b = (int(value) for value in raw)
    except ValueError:
        return 2
"""

APP_ADD = """
    if op == "add":
        print(a + b)
        return 0
"""

APP_MUL = """
    if op == "mul":
        print(a * b)
        return 0
"""

APP_FOOTER = """
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
"""

# Behaves differently when coverage instruments the process: models a verdict
# that flips under instrumentation (oracle-validation.md §4, circuit_eval).
APP_COVERAGE_SENSITIVE = """
    if op == "add" and os.environ.get("COVERAGE_PROCESS_START"):
        print(a + b + 1)
        return 0
"""


def write_app(root: Path, *, mul: bool = False, coverage_sensitive: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    body = APP_HEADER
    if coverage_sensitive:
        body += APP_COVERAGE_SENSITIVE
    body += APP_ADD + (APP_MUL if mul else "") + APP_FOOTER
    (root / "app.py").write_text(textwrap.dedent(body).lstrip())
    return root


def write_tests(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("")
    (root / "conftest.py").write_text(CONFTEST.lstrip())
    (root / "test_checkpoint_1.py").write_text(TEST_CHECKPOINT_1.lstrip())
    (root / "test_checkpoint_2.py").write_text(TEST_CHECKPOINT_2.lstrip())
    return root


def step_spec(name: str, files: list[str]) -> OracleStepSpec:
    return OracleStepSpec(
        name=name,
        checkpoint=name,
        test_files=tuple(files),
        pytest_args=(
            *(f"{{tests}}/{f}" for f in files),
            "--checkpoint",
            name,
            "--entrypoint",
            f"{sys.executable} {{app}}/app.py",
        ),
        timeout_s=None,
    )


def make_bundle(root: Path) -> Any:
    tests = write_tests(root / "src-tests")
    manifest = BundleManifest(
        problem="adder",
        source={"format": "test-fixture"},
        env={"TERM": "xterm"},
        grader=GraderSpec(base_image="local"),
        steps=(
            step_spec("checkpoint_1", ["test_checkpoint_1.py"]),
            step_spec("checkpoint_2", ["test_checkpoint_1.py", "test_checkpoint_2.py"]),
        ),
    )
    return write_bundle(root / "bundle", manifest, tests)


@dataclass
class LocalProcessSandbox:
    """Grader invocation as a host subprocess over a fixed host layout."""

    root: Path
    runs: list[SandboxRun] = field(default_factory=list)

    @property
    def layout(self) -> SandboxLayout:
        return SandboxLayout(
            app=str(self.root / "app"),
            tests=str(self.root / "tests"),
            runtime=str(RUNTIME_DIR),
            out=str(self.root / "out"),
            scratch=str(self.root / "scratch"),
            python=sys.executable,
        )

    async def environment_id(self) -> str:
        return f"local:{sys.executable}"

    def _stage(self, run: SandboxRun) -> None:
        layout = self.layout
        for path in (layout.app, layout.tests, layout.out, layout.scratch):
            shutil.rmtree(path, ignore_errors=True)
        shutil.copytree(run.app_dir, layout.app, symlinks=True)
        shutil.copytree(run.tests_dir, layout.tests)
        Path(layout.out).mkdir(parents=True)

    async def run(self, run: SandboxRun) -> SandboxResult:
        self.runs.append(run)
        self._stage(run)
        env = {"PATH": os.environ.get("PATH", ""), **run.env}
        proc = await asyncio.create_subprocess_exec(
            *run.argv,
            cwd=self.layout.app,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=run.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return SandboxResult(None, True, "")
        shutil.copytree(self.layout.out, run.out_dir, dirs_exist_ok=True)
        return SandboxResult(proc.returncode, False, stdout.decode(errors="replace"))


@dataclass
class ExecResult:
    stdout: str
    stderr: str = ""
    return_code: int = 0


@dataclass
class FakeAgentEnvironment:
    """Records every call a verifier makes against the agent environment."""

    leak_stdout: str = "RAIDAR_LEAK_SCAN_DONE\n"
    app_source: Path | None = None
    calls: list[str] = field(default_factory=list)

    async def exec(self, command: str, **kwargs: Any) -> ExecResult:
        self.calls.append("exec")
        return ExecResult(stdout=self.leak_stdout)

    async def download_dir_with_exclusions(
        self, *, source_dir: str, target_dir: Path, exclude: list[str]
    ) -> None:
        self.calls.append(f"download:{source_dir}")
        assert self.app_source is not None
        shutil.copytree(self.app_source, target_dir, dirs_exist_ok=True)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("upload"):
            self.calls.append(name)
            raise AssertionError(f"verifier called {name} on the agent environment")
        raise AttributeError(name)
