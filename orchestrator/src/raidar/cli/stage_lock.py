"""``stage-lock``: generate a stage tool's wheels-only ``uv.lock``, and check one is current.

uv only resolves: it runs in a scratch copy of the dependency-only ``tool/`` project with
``--no-build`` (sdists are never built, so no package code runs on the host). The generated
lock drops uv's sdist metadata and must satisfy ``declared-wheels/1`` for every pack
architecture, and ``uv lock --check --offline`` must accept the result.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from raidar.contract.wheels import WheelPolicyError, generated_wheels_only_lock, uv_lock_refusals

LOCK_FILE = "uv.lock"
PYTHON = "3.12"
UV_TIMEOUT_S = 600


class StageLockError(RuntimeError):
    """The lock cannot be generated or is not current."""


def _scratch_project(tool: Path, scratch: Path, *, with_lock: bool) -> Path:
    """Copy only ``pyproject.toml`` (and optionally ``uv.lock``): no authored source."""
    project = scratch / "tool"
    project.mkdir()
    shutil.copyfile(tool / "pyproject.toml", project / "pyproject.toml")
    if with_lock:
        shutil.copyfile(tool / LOCK_FILE, project / LOCK_FILE)
    return project


def _uv(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["uv", *args, "--project", str(project), "--python", PYTHON, "--no-build"],
            capture_output=True,
            text=True,
            check=False,
            timeout=UV_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StageLockError(f"uv could not run: {error}") from error


def _detail(done: subprocess.CompletedProcess[str]) -> str:
    return (done.stderr or done.stdout).strip()[-1500:]


def generate_lock(package_root: Path) -> str:
    """Write ``tool/uv.lock`` for the package at ``package_root``; its text."""
    tool = package_root / "tool"
    if not (tool / "pyproject.toml").is_file():
        raise StageLockError(f"{package_root.name} has no tool/pyproject.toml to lock")
    with tempfile.TemporaryDirectory(prefix="raidar-stage-lock-") as scratch:
        project = _scratch_project(tool, Path(scratch), with_lock=False)
        done = _uv(project, "lock", "--quiet")
        if done.returncode != 0:
            raise StageLockError(f"uv lock failed: {_detail(done)}")
        try:
            text = generated_wheels_only_lock((project / LOCK_FILE).read_text(encoding="utf-8"))
        except WheelPolicyError as error:
            raise StageLockError(f"the lock is not wheels-only: {error}") from error
        (project / LOCK_FILE).write_text(text, encoding="utf-8")
        checked = _uv(project, "lock", "--check", "--offline")
        if checked.returncode != 0:
            raise StageLockError(f"uv rejects the generated lock offline: {_detail(checked)}")
    (tool / LOCK_FILE).write_text(text, encoding="utf-8")
    return text


def lock_problems(package_root: Path) -> list[str]:
    """Why ``tool/uv.lock`` is unusable (empty: current and wheels-only)."""
    tool = package_root / "tool"
    if not (tool / "pyproject.toml").is_file():
        return []
    if not (tool / LOCK_FILE).is_file():
        return ["tool/uv.lock is missing: run make stage-lock"]
    text = (tool / LOCK_FILE).read_text(encoding="utf-8")
    problems = [f"tool/uv.lock: {refusal}" for refusal in uv_lock_refusals(text)]
    with tempfile.TemporaryDirectory(prefix="raidar-stage-lock-") as scratch:
        project = _scratch_project(tool, Path(scratch), with_lock=True)
        checked = _uv(project, "lock", "--check", "--offline")
    if checked.returncode != 0:
        problems.append(f"tool/uv.lock is stale: {_detail(checked)}")
    return problems
