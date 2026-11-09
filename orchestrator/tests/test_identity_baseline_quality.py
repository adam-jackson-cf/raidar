"""Real-process canaries for identity checking and baseline CLI refusal."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]


def test_identity_check_accepts_the_real_repository_record() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/checks/check-identity.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_baseline_cli_accepts_equal_scan_and_refuses_new_finding(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("def build():\n    unused = 1\n    return 0\n")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schemaVersion": 1, "tool": "ruff", "findings": []}))
    base_baseline = tmp_path / "base-baseline.json"
    base_baseline.write_text(baseline.read_text())
    report = tmp_path / "report.json"
    report.write_text("[]")
    command = [
        sys.executable,
        str(ROOT / "scripts/checks/check-finding-baseline.py"),
        "--tool",
        "ruff",
        "--root",
        str(tmp_path),
        "--baseline",
        str(baseline),
        "--base-baseline",
        str(base_baseline),
        "--report",
        str(report),
    ]
    accepted = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert accepted.stdout == ""
    assert accepted.stderr == ""

    report.write_text(
        json.dumps(
            [
                {
                    "filename": str(source),
                    "code": "F841",
                    "message": "Local variable unused is assigned to but never used",
                    "location": {"row": 2, "column": 5},
                    "end_location": {"row": 2, "column": 11},
                    "fix": None,
                    "noqa_row": 2,
                    "url": "https://docs.astral.sh/ruff/rules/unused-variable/",
                }
            ]
        )
    )
    refused = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert refused.stdout == ""
    assert "BASELINE.new" in refused.stderr
    assert "example.py" in refused.stderr
    assert "F841" in refused.stderr
    assert len(refused.stderr.splitlines()) == 1
