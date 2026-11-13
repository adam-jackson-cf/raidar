"""Quality canary rejects corrupted vendored validation before it runs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = "scripts/checks/validate-rule-coverage.py"
VENDOR = "scripts/vendor/setup-repository-guardrails/validate_rule_coverage.py"


@pytest.mark.parametrize("defect", ["vendor"])
def test_invalid_retained_evidence_fails_before_validation(tmp_path: Path, defect: str) -> None:
    for relative in (LAUNCHER, VENDOR, ".omp/host-readback.json"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    with (tmp_path / VENDOR).open("a") as stream:
        stream.write(f"\n# corrupted {defect}\n")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "AGENT" and not key.startswith("GIT_")
    }
    result = subprocess.run(
        [
            sys.executable,
            LAUNCHER,
            "--root",
            ".",
            "--catalog",
            ".omp/guardrail-rules.json",
            "--ledger",
            ".omp/rule-coverage.json",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    assert "vendored validator differs" in result.stderr
