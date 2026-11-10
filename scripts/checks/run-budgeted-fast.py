#!/usr/bin/env python3
"""Run pytest once and normalize its inventory, phases and outer wall time."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# GitHub's runner-image metadata names are externally defined mixed-case keys.
IMAGE_FAMILY_ENV = "ImageOS"
IMAGE_VERSION_ENV = "ImageVersion"


def default_profile() -> dict[str, Any]:
    """Identify the production measurement configuration, not the physical host."""
    from raidar.cli.quality import FAST_SELECTOR, PYPROJECT

    configuration = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]
    coverage = {
        "coverage": configuration["coverage"],
        "floors": configuration["raidar"]["coverage"],
    }
    coverage_bytes = json.dumps(coverage, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schemaVersion": 1,
        "runnerClass": os.environ.get(
            "RAIDAR_TEST_BUDGET_RUNNER_CLASS",
            os.environ.get("RUNNER_ENVIRONMENT", "local"),
        ),
        "osImageFamily": os.environ.get(
            "RAIDAR_TEST_BUDGET_OS_IMAGE_FAMILY",
            os.environ.get(IMAGE_FAMILY_ENV, platform.system()),
        ),
        "arch": os.environ.get("RUNNER_ARCH", platform.machine()),
        "pythonVersion": platform.python_version(),
        "lockDigest": hashlib.sha256((ROOT / "orchestrator/uv.lock").read_bytes()).hexdigest(),
        "coverageDigest": hashlib.sha256(coverage_bytes).hexdigest(),
        "selector": FAST_SELECTOR,
        "workers": 1,
    }


def machine_details() -> dict[str, str]:
    cpu = platform.processor()
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            cpu = result.stdout.strip()
    elif sys.platform.startswith("linux"):
        try:
            cpu_info = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        except OSError:
            cpu_info = ""
        for line in cpu_info.splitlines():
            label, separator, value = line.partition(":")
            if separator and label.strip() in {"model name", "Hardware"}:
                cpu = value.strip()
                break
    return {
        "imageVersion": os.environ.get(IMAGE_VERSION_ENV, platform.platform()),
        "cpuModel": cpu or platform.machine(),
    }


def run_pytest(arguments: list[str], phase_path: Path) -> tuple[int, float]:
    environment = dict(os.environ)
    environment["RAIDAR_TEST_BUDGET_PHASE_REPORT"] = str(phase_path)
    plugin_directory = str(Path(__file__).resolve().parent)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (plugin_directory, environment.get("PYTHONPATH", "")) if part
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "guardrail_test_report", *arguments],
            env=environment,
            check=False,
        )
        exit_code = result.returncode
    except KeyboardInterrupt:
        exit_code = 130
    except OSError as error:
        print(f"TEST.budget.fast-tier-time: cannot start pytest: {error}", file=sys.stderr)
        exit_code = 2
    return exit_code, time.monotonic() - started


def current_revision() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--invocation", required=True)
    parser.add_argument("--revision")
    parser.add_argument("--profile-json", type=json.loads)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    pytest_args = arguments.pytest_args
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    revision = arguments.revision or current_revision()
    profile = arguments.profile_json if arguments.profile_json is not None else default_profile()
    machine = machine_details()
    report_path = arguments.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pytest-phases-", dir=report_path.parent) as temporary:
        phase_path = Path(temporary) / "phases.json"
        exit_code, wall_seconds = run_pytest(pytest_args, phase_path)
        try:
            phases = json.loads(phase_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            phases = {}
        complete = exit_code == 0 and phases.get("complete") is True
        report = {
            "schemaVersion": 1,
            "invocationId": arguments.invocation,
            "revision": revision,
            "profile": profile,
            "machine": machine,
            "wallSeconds": wall_seconds,
            "inventory": phases.get("inventory", []),
            "cases": phases.get("cases", {}),
            "exitCode": exit_code,
            "complete": complete,
        }
        if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_JOB"):
            report["measurement"] = {
                "runId": os.environ["GITHUB_RUN_ID"],
                "jobId": os.environ["GITHUB_JOB"],
                "successful": complete,
            }
        report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
