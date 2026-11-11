#!/usr/bin/env python3
"""Validate gate wiring and exercise isolated commit/dispatcher canaries."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow_matches(workflow: str) -> bool:
    """Require an unconditional gate dispatcher, allowing conditional artifact uploads."""
    document = yaml.safe_load(workflow)
    events = document.get("on", document.get(True, {}))
    job = document.get("jobs", {}).get("gate", {})
    command = (
        "env -u AGENT uv run --project orchestrator --extra dev python "
        "scripts/checks/run-repository-gate.py ci"
    )
    return (
        isinstance(events, dict)
        and {"push", "pull_request"} <= events.keys()
        and "if" not in job
        and any(
            step.get("run", "").strip() == command and "if" not in step
            for step in job.get("steps", [])
        )
    )


def ci_contract_matches(ci: object) -> bool:
    """Require the host-neutral CI contract: the gate context and a named runner role."""
    return (
        isinstance(ci, dict)
        and set(ci) == {"requiredContext", "runner"}
        and ci["requiredContext"] == "gate"
        and isinstance(ci["runner"], str)
        and bool(ci["runner"].strip())
    )


def wiring_findings(gate: dict[str, Any], workflow: str, hook: str) -> list[str]:
    """Keep the declared argv, installed entry and always-running CI gate aligned."""
    errors = []
    expected = {
        "commit": ["env", "-u", "AGENT", "make", "gate-commit"],
        "ci": ["env", "-u", "AGENT", "make", "gate-ci"],
    }
    if (
        gate.get("schemaVersion") != 2
        or gate.get("checks") != expected
        or not ci_contract_matches(gate.get("ci"))
    ):
        errors.append("GATE.local-ci-parity: restore the declared commit/CI make argv")
    if not workflow_matches(workflow):
        errors.append(
            "GATE.local-ci-parity: run the declared dispatcher in an unconditional gate "
            "on every push/PR"
        )
    if (
        not gate.get("pathCheck")
        or gate["pathCheck"][-1] != "scripts/checks/check-repository-paths.py"
    ):
        errors.append("GATE.path-check: restore the executable changed-path checker argv")
    if (
        "scripts/checks/run-repository-gate.py commit" not in hook
        or "pass_filenames: false" not in hook
    ):
        errors.append("GATE.pre-commit: restore the tracked exact commit dispatcher")
    return errors


def run_process(argv: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    """Execute bounded isolated canaries without inherited git context."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "AGENT"
    }
    return subprocess.run(
        argv, cwd=root, env=environment, text=True, capture_output=True, check=False, timeout=45
    )


def isolated_hook(status: int | None) -> int:
    """Install the tracked manager only in a disposable checkout."""
    with tempfile.TemporaryDirectory(prefix="raidar-manager-") as temporary:
        scratch = Path(temporary)
        for name in (
            ".pre-commit-config.yaml",
            "scripts/checks/run-repository-gate.py",
            "scripts/checks/check-hook-installation.py",
        ):
            target = scratch / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        result = run_process(["git", "init", "-q"], scratch)
        if result.returncode:
            return result.returncode
        result = run_process(
            [sys.executable, "-m", "pre_commit", "install", "--hook-type", "pre-commit"], scratch
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr)
            return result.returncode
        if status is None:
            result = run_process(
                [
                    sys.executable,
                    "scripts/checks/check-hook-installation.py",
                    "--root",
                    str(scratch),
                ],
                scratch,
            )
        else:
            project = scratch / "orchestrator"
            project.mkdir()
            (project / "pyproject.toml").write_text(
                '[project]\nname="hook-fixture"\nversion="0.0.0"\n[project.optional-dependencies]\ndev=[]\n'
            )
            policy = scratch / ".omp"
            policy.mkdir()
            (policy / "gate.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "checks": {"commit": [sys.executable, "-c", f"raise SystemExit({status})"]},
                    }
                )
            )
            for key, value in (
                ("user.name", "Canary"),
                ("user.email", "canary@example.invalid"),
                ("commit.gpgsign", "false"),
            ):
                run_process(["git", "config", key, value], scratch).check_returncode()
            run_process(["git", "add", "."], scratch).check_returncode()
            result = run_process(["git", "commit", "-m", "test: canary"], scratch)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            print("GATE.pre-commit: failing checks.commit blocks the commit", file=sys.stderr)
        return result.returncode


def main() -> int:
    """Check real wiring or one fixture against the same production decision."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--hook-install", action="store_true")
    args = parser.parse_args()
    if args.hook_install:
        return isolated_hook(None)
    if args.fixture:
        fixture = json.loads(args.fixture.read_text())
        if fixture.get("mode") == "hook":
            return isolated_hook(fixture["status"])
        if fixture.get("mode") == "path":
            gate = json.loads((ROOT / ".omp/gate.json").read_text())
            result = run_process([*gate["pathCheck"], *fixture["paths"]], ROOT)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        if fixture.get("mode") == "failure":
            with tempfile.TemporaryDirectory(prefix="raidar-dispatch-") as temporary:
                scratch = Path(temporary)
                (scratch / ".omp").mkdir()
                (scratch / ".omp/gate.json").write_text(
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "checks": {
                                "ci": [
                                    sys.executable,
                                    "-c",
                                    f"raise SystemExit({fixture['status']})",
                                ]
                            },
                        }
                    )
                )
                dispatch = runpy.run_path(str(ROOT / "scripts/checks/run-repository-gate.py"))
                status = int(dispatch["main"](["ci"], root=scratch))
                if status:
                    print("TEST.failure-propagation: failed prerequisite blocks gate")
                return status
        errors = wiring_findings(fixture["gate"], fixture["workflow"], fixture["hook"])
    else:
        errors = wiring_findings(
            json.loads((ROOT / ".omp/gate.json").read_text()),
            (ROOT / ".github/workflows/ci-quality-gates-incremental.yml").read_text(),
            (ROOT / ".pre-commit-config.yaml").read_text(),
        )
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
