#!/usr/bin/env python3
"""Check Python pins and the host, CI and problem resolver uv toolchain pins.

Python pin files must be byte-equal and match the running interpreter.
orchestrator/pyproject.toml [tool.uv].required-version is the exact uv pin;
host uv, every CI setup-uv step and tracked problem JSON resolvers must agree.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import yaml


def pin_violations(
    root_pin: bytes | None, project_pin: bytes | None, interpreter: str
) -> list[str]:
    found = []
    if root_pin is None:
        found.append("missing .python-version")
    if project_pin is None:
        found.append("missing orchestrator/.python-version")
    if root_pin is not None and project_pin is not None and root_pin != project_pin:
        found.append(".python-version and orchestrator/.python-version are not byte-equal")
    pin = root_pin if root_pin is not None else project_pin
    if pin is not None and interpreter != pin.decode().strip():
        found.append(f"running Python {interpreter} differs from pin {pin.decode().strip()}")
    return found


def _resolver_versions(record: object) -> Iterator[str]:
    if isinstance(record, dict):
        for key, value in record.items():
            if key == "resolver" and isinstance(value, str) and value.startswith("uv@"):
                yield value.removeprefix("uv@")
            yield from _resolver_versions(value)
    elif isinstance(record, list):
        for value in record:
            yield from _resolver_versions(value)


def _uv_pin_violations(repo_root: Path) -> tuple[str, list[str]]:
    project = tomllib.loads((repo_root / "orchestrator/pyproject.toml").read_text(encoding="utf-8"))
    required = project.get("tool", {}).get("uv", {}).get("required-version", "")
    exact = re.fullmatch(r"==(\d+\.\d+\.\d+)", required)
    if exact is None:
        return "<version>", [
            "orchestrator/pyproject.toml [tool.uv].required-version must be an exact ==X.Y.Z pin"
        ]
    pin = exact.group(1)
    host = subprocess.run(
        ["uv", "--version"], check=True, capture_output=True, text=True
    ).stdout.split()[1]
    versions: list[tuple[str, object]] = [("host uv", host)]
    workflow_dir = repo_root / ".github/workflows"
    for workflow in sorted([*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")]):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in document.get("jobs", {}).items():
            for index, step in enumerate(job.get("steps", []), start=1):
                if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                    versions.append(
                        (
                            f"{workflow.relative_to(repo_root)} job {job_name} "
                            f"step {index} setup-uv",
                            step.get("with", {}).get("version"),
                        )
                    )
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "lab/problems/registry"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for relative in tracked.split("\0"):
        if relative.endswith(".json"):
            record = json.loads((repo_root / relative).read_text(encoding="utf-8"))
            versions.extend((relative, version) for version in _resolver_versions(record))
    return pin, [
        f"{source}: uv {version!r} differs from required uv {pin}"
        for source, version in versions
        if version != pin
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)
    pins: list[bytes | None] = []
    for relative in (".python-version", "orchestrator/.python-version"):
        try:
            pins.append((args.repo_root / relative).read_bytes())
        except FileNotFoundError:
            pins.append(None)
    found = pin_violations(pins[0], pins[1], platform.python_version())
    uv_pin, uv_found = _uv_pin_violations(args.repo_root)
    found.extend(uv_found)
    if not found:
        print("toolchain-pin-check: ok")
        return 0
    print("toolchain-pin-check: toolchain pin rule violated:", file=sys.stderr)
    for line in found:
        print(f"  {line}", file=sys.stderr)
    pin = next((value.decode().strip() for value in pins if value is not None), "<pin>")
    print(
        "Restore byte-equal pin files, then run:\n"
        f"  uv python install {pin}\n"
        f"  uv self update {uv_pin}\n"
        "  env -u AGENT uv sync --project orchestrator --all-extras --frozen",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
