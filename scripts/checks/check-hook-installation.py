#!/usr/bin/env python3
"""Prove installed pre-commit wiring and commit failure propagation in isolation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DIAGNOSTIC = "GATE.pre-commit: installed manager propagates commit failure"
ENTRY = (
    "env -u AGENT uv run --project orchestrator --extra dev python "
    "scripts/checks/run-repository-gate.py commit"
)


def run_process(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    """Bound all hook/git children and remove enclosing git context."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key != "AGENT"
    }
    return subprocess.run(
        command, cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=30
    )


def installed_hook(root: Path) -> Path:
    """Refuse absent or miswired installed manager/configuration."""
    result = run_process(["git", "rev-parse", "--git-path", "hooks/pre-commit"], root)
    hook = root / result.stdout.strip()
    if (
        result.returncode
        or not hook.is_file()
        or "pre_commit" not in hook.read_text()
        or not os.access(hook, os.X_OK)
    ):
        raise ValueError("install the tracked pre-commit manager in this checkout")
    configuration = (root / ".pre-commit-config.yaml").read_text()
    if (
        f"entry: {ENTRY}" not in configuration
        or "pass_filenames: false" not in configuration
        or "always_run: true" not in configuration
    ):
        raise ValueError("restore the tracked repository-gate entry and failure propagation")
    return hook


def prove_installation(root: Path) -> None:
    """Copy existing manager into a disposable checkout; never alter the caller."""
    hook = installed_hook(root)
    with tempfile.TemporaryDirectory(prefix="raidar-hook-") as temporary:
        scratch = Path(temporary)
        result = run_process(["git", "init", "-q"], scratch)
        if result.returncode:
            raise ValueError(result.stderr)
        for key, value in (
            ("user.name", "Rule Evidence"),
            ("user.email", "rule-evidence@example.invalid"),
            ("commit.gpgsign", "false"),
        ):
            if run_process(["git", "config", key, value], scratch).returncode:
                raise ValueError("configure isolated commit identity")
        shutil.copy2(hook, scratch / ".git/hooks/pre-commit")
        shutil.copy2(root / ".pre-commit-config.yaml", scratch)
        scripts = scratch / "scripts/checks"
        scripts.mkdir(parents=True)
        shutil.copy2(root / "scripts/checks/run-repository-gate.py", scripts)
        project = scratch / "orchestrator"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "hook-canary"\nversion = "0.0.0"\n'
            'requires-python = ">=3.12"\n[project.optional-dependencies]\ndev = []\n'
        )
        policy = scratch / ".omp"
        policy.mkdir()
        for status in (0, 17):
            (policy / "gate.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "checks": {"commit": [sys.executable, "-c", f"raise SystemExit({status})"]},
                    }
                )
            )
            if run_process(["git", "add", "."], scratch).returncode:
                raise ValueError("stage isolated hook fixture")
            result = run_process(["git", "commit", "-m", "test: isolated hook canary"], scratch)
            if (status == 0) != (result.returncode == 0):
                raise ValueError(
                    f"commit failure propagation mismatch: {result.stdout} {result.stderr}"
                )


def main() -> int:
    """Inspect the selected checkout and report only a completed installation proof."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        prove_installation(args.root.resolve())
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"GATE.pre-commit: {error}", file=sys.stderr)
        return 1
    print(DIAGNOSTIC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
