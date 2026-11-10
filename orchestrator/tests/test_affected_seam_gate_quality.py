"""Real make boundary canaries for affected T2 commit refusal and full CI tiers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def gate_boundary(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """Keep real make, replacing expensive child tools with isolated executables."""
    make = shutil.which("make")
    assert make is not None
    binaries = tmp_path / "bin"
    binaries.mkdir()
    trace = tmp_path / "commands"
    trace.write_text("", encoding="utf-8")
    uv = binaries / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "uv %s\\n" "$*" >> "$U5_TRACE"\n'
        'case "$*" in *run-affected-seams.py*) exit "$U5_SEAM_EXIT";; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    recursive_make = binaries / "nested-make"
    recursive_make.write_text(
        '#!/bin/sh\nprintf "make %s\\n" "$*" >> "$U5_TRACE"\nexit 0\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    recursive_make.chmod(0o755)
    environment = dict(os.environ)
    environment.pop("AGENT", None)
    environment.update(
        PATH=f"{binaries}{os.pathsep}{environment.get('PATH', '')}",
        U5_TRACE=str(trace),
        U5_SEAM_EXIT="0",
        U5_MAKE=make,
    )
    return recursive_make, environment, trace


@pytest.mark.parametrize("seam_exit", [0, 17])
def test_commit_gate_runs_affected_t2_and_refuses_its_failure(
    gate_boundary: tuple[Path, dict[str, str], Path], seam_exit: int
) -> None:
    """GATE.pre-commit: affected T2 success permits, failure blocks the commit gate."""
    recursive_make, environment, trace = gate_boundary
    environment["U5_SEAM_EXIT"] = str(seam_exit)
    result = subprocess.run(
        [
            environment["U5_MAKE"],
            "--no-print-directory",
            "gate-commit",
            f"MAKE={recursive_make}",
            "REPO_RUNTIME_ENV=env",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    commands = trace.read_text(encoding="utf-8").splitlines()
    assert any("scripts/checks/run-affected-seams.py" in command for command in commands)
    assert (result.returncode == 0) is (seam_exit == 0)


def test_ci_gate_runs_full_slow_and_quality_tiers(
    gate_boundary: tuple[Path, dict[str, str], Path],
) -> None:
    """TEST.tiers: CI cannot substitute affected-only selection for the full T2 set."""
    recursive_make, environment, trace = gate_boundary
    result = subprocess.run(
        [
            environment["U5_MAKE"],
            "--no-print-directory",
            "gate-ci",
            f"MAKE={recursive_make}",
            "REPO_RUNTIME_ENV=env",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    commands = [line.split() for line in trace.read_text(encoding="utf-8").splitlines()]
    for tier in ("test-slow", "test-quality"):
        assert any(command[0] == "make" and tier in command[1:] for command in commands)
