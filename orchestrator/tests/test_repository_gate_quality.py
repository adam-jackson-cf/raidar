"""Real-process canaries for repository gate exit codes and path diagnostics."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("returncode", [0, 17])
def test_dispatcher_preserves_child_exit_status(tmp_path: Path, returncode: int) -> None:
    script = tmp_path / "scripts/checks/run-repository-gate.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/checks/run-repository-gate.py", script)
    gate_directory = tmp_path / ".omp"
    gate_directory.mkdir()
    (gate_directory / "gate.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "checks": {"commit": [sys.executable, "-c", f"raise SystemExit({returncode})"]},
            }
        )
    )

    result = subprocess.run(
        [sys.executable, str(script), "commit"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == returncode, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_path_check_accepts_a_clean_owned_path() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), "README.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_path_check_refuses_a_misplaced_file_with_one_actionable_line() -> None:
    # This canary must be at the root to exercise real folder-placement policy.
    with tempfile.NamedTemporaryFile(
        dir=ROOT, prefix="gate-canary-", suffix=".txt", mode="w", encoding="utf-8"
    ) as candidate:
        candidate.write("misplaced file\n")
        candidate.flush()
        relative = Path(candidate.name).relative_to(ROOT).as_posix()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/checks/check-repository-paths.py"), relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        rf"{re.escape(relative)}: STRUCT\.folder-placement: [^\r\n;]+; [^\r\n]+", lines[0]
    )
