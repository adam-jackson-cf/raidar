#!/usr/bin/env python3
"""Run one catalogue canary through the maintained analyzer or parity checker.

Fixtures contain source files, not expected answers. Copying them outside tests
preserves the production exclusions and per-file lint policy. No baseline debt
is imported: every fixture starts with zero permitted findings.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]


def load_checker(filename: str) -> ModuleType:
    """Load the production checker without replacing its decision functions."""
    spec = importlib.util.spec_from_file_location(
        "tool_canary_checker", ROOT / "scripts/checks" / filename
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parity_findings(rule: str, fixture: dict[str, Any]) -> list[str]:
    """Invoke the same pure decisions used by the production static/audit gate."""
    checker = load_checker("check-toolchain-parity.py")
    if rule == "PARITY.dependency-inventory":
        return cast(
            "list[str]",
            checker.dependency_inventory_findings(fixture["project"], fixture["installers"]),
        )
    if rule == "PARITY.install-guidance":
        return cast(
            "list[str]",
            checker.install_guidance_findings(
                fixture["guidance"], fixture["installers"], fixture["targets"]
            ),
        )
    if rule == "PARITY.lock-version":
        return cast(
            "list[str]", checker.lock_version_findings(fixture["projects"], fixture["lock"])
        )
    if rule == "PRETTIER.version":
        return cast("list[str]", checker.prettier_version_findings(fixture["sources"]))
    # Exercise the real fail-closed audit report parser and vulnerability policy,
    # without depending on mutable network advisory responses in local canaries.
    report = json.dumps(fixture["report"])
    problem = checker.audit_report_problem(report)
    if problem:
        return [checker.finding(checker.LOCK, checker.AUDIT, problem, "repair the audit inventory")]
    return cast("list[str]", checker.vulnerability_findings(report))


def analyzer_canary(rule: str, fixture: dict[str, Any], workspace: Path) -> int:
    """Check isolated fixture sources with the unchanged repository configuration."""
    config = workspace / "orchestrator/pyproject.toml"
    config.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "orchestrator/pyproject.toml", config)
    for relative, source in fixture["files"].items():
        target = workspace / relative
        if not target.resolve().is_relative_to(workspace):
            raise ValueError("fixture path escapes workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    if rule == "MYPY.package-coverage":
        checker = load_checker("check-mypy-coverage.py")
        findings = checker.coverage_findings(checker.inventory(workspace), fixture["targets"])
        print("\n".join(findings))
        return int(bool(findings))
    command = [sys.executable, "-m"]
    if rule.startswith("MYPY."):
        command += [
            "mypy",
            "--config-file",
            str(config),
            "--no-incremental",
            "--cache-dir=/dev/null",
            "--follow-imports=skip",
            "--no-pretty",
            "--no-color-output",
        ]
    elif fixture.get("format"):
        command += ["ruff", "format", "--check", "--config", str(config)]
    else:
        command += ["ruff", "check", "--no-fix", "--force-exclude", "--config", str(config)]
    command += fixture.get("targets", ["orchestrator/src/probe.py"])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "AGENT" and not key.startswith("GIT_")
    }
    result = subprocess.run(
        command,
        cwd=workspace,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rule")
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    if args.rule == "RUFF.IGNORES":
        checker = load_checker("check-ruff-suppressions.py")
        findings = checker.added_suppression_findings(
            fixture["base"], fixture["head"], fixture["paths"]
        )
        print("\n".join(findings))
        return int(bool(findings))
    if args.rule.startswith(("PARITY.", "PRETTIER.", "SECURITY.")):
        findings = parity_findings(args.rule, fixture)
        print("\n".join(findings))
        return int(bool(findings))
    with tempfile.TemporaryDirectory(prefix="raidar-tool-canary-") as directory:
        return analyzer_canary(args.rule, fixture, Path(directory).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
