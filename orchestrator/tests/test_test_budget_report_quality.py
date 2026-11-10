"""Process canary for complete and truncated fast-tier measurement reports."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.quality
ROOT = Path(__file__).resolve().parents[2]


def test_fast_report_producer_records_inventory_phases_and_early_stop(tmp_path: Path) -> None:
    suite = tmp_path / "test_sample.py"
    suite.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    yield 1\n"
        "def test_a(resource):\n"
        "    assert resource == 1\n"
        "def test_b():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    profile: dict[str, Any] = {
        "schemaVersion": 1,
        "runnerClass": "canary",
        "osImageFamily": "canary",
        "arch": "canary",
        "pythonVersion": "3.13.7",
        "lockDigest": "canary-lock",
        "coverageDigest": "canary-coverage",
        "selector": "not slow and not quality and not docker and not security and not perf",
        "workers": 1,
    }
    environment = {name: value for name, value in os.environ.items() if name != "AGENT"}
    reports: list[dict[str, Any]] = []
    for stopped in (False, True):
        if stopped:
            suite.write_text("def test_a():\n    assert False\ndef test_b():\n    assert True\n")
        report_path = tmp_path / ("stopped.json" if stopped else "complete.json")
        invocation = "stopped" if stopped else "complete"
        command = [
            sys.executable,
            str(ROOT / "scripts/checks/run-budgeted-fast.py"),
            "--report",
            str(report_path),
            "--invocation",
            invocation,
            "--revision",
            "canary-revision",
            "--profile-json",
            json.dumps(profile),
            "--",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            profile["selector"],
            *(["-x"] if stopped else []),
            str(suite),
        ]
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == (1 if stopped else 0), completed.stdout + completed.stderr
        assert report_path.is_file(), completed.stdout + completed.stderr
        report: dict[str, Any] = json.loads(report_path.read_text())
        assert report["invocationId"] == invocation
        assert report["revision"] == "canary-revision"
        assert report["profile"] == profile
        reports.append(report)
    complete, truncated = reports
    assert complete["complete"] is True
    assert complete["exitCode"] == 0
    assert len(complete["inventory"]) == 2
    assert set(complete["inventory"]) == set(complete["cases"])
    totals = []
    for case in complete["cases"].values():
        assert case["outcome"] == "passed"
        assert set(case["phases"]) == {"setup", "call", "teardown"}
        assert all(math.isfinite(value) and value >= 0 for value in case["phases"].values())
        totals.append(sum(case["phases"].values()))
    assert complete["wallSeconds"] >= sum(totals)
    assert complete["machine"]["imageVersion"]
    assert complete["machine"]["cpuModel"]
    assert truncated["complete"] is False
    assert truncated["exitCode"] == 1
    assert len(truncated["inventory"]) == 2
    executed = [case for case in truncated["cases"].values() if "call" in case["phases"]]
    assert len(executed) == 1
    assert executed[0]["outcome"] == "failed"
