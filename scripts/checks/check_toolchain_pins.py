#!/usr/bin/env python3
"""Check Python pins and the declared, locked and problem resolver uv pins.

Python pin files must be byte-equal and match the running interpreter.
The exact uv project dependency must match its locked package version and every
tracked problem JSON resolver.
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


def _resolvers(record: object) -> Iterator[object]:
    if isinstance(record, dict):
        for key, value in record.items():
            if key == "resolver":
                yield value
            yield from _resolvers(value)
    elif isinstance(record, list):
        for value in record:
            yield from _resolvers(value)


def _declared_uv_version(dependencies: list[str]) -> str | None:
    declarations = [
        dependency
        for dependency in dependencies
        if re.match(r"uv(?=[\s\[<>=!~;@]|$)", dependency, re.IGNORECASE)
    ]
    if len(declarations) != 1:
        return None
    exact = re.fullmatch(r"uv==(\d+\.\d+\.\d+)", declarations[0])
    return exact.group(1) if exact is not None else None


def _uv_pin_violations(repo_root: Path) -> list[str]:
    project = tomllib.loads((repo_root / "orchestrator/pyproject.toml").read_text(encoding="utf-8"))
    pin = _declared_uv_version(project.get("project", {}).get("dependencies", []))
    if pin is None:
        return [
            "orchestrator/pyproject.toml [project].dependencies must declare exactly one "
            "exact uv==X.Y.Z pin"
        ]
    lock = tomllib.loads((repo_root / "orchestrator/uv.lock").read_text(encoding="utf-8"))
    locked = [
        package.get("version") for package in lock.get("package", []) if package.get("name") == "uv"
    ]
    found = []
    if locked != [pin]:
        found.append(f"orchestrator/uv.lock: uv versions {locked!r} differ from declared uv {pin}")
    found.extend(_registry_resolver_violations(repo_root, pin))
    return found


def _registry_resolver_violations(repo_root: Path, pin: str) -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "lab/problems/registry"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    found = []
    for relative in tracked.split("\0"):
        if relative.endswith(".json"):
            record = json.loads((repo_root / relative).read_text(encoding="utf-8"))
            found.extend(
                f"{relative}: resolver {resolver!r} differs from declared uv@{pin}"
                for resolver in _resolvers(record)
                if resolver != f"uv@{pin}"
            )
    return found


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
    found.extend(_uv_pin_violations(args.repo_root))
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
        "  env -u AGENT uv sync --project orchestrator --all-extras --frozen",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
