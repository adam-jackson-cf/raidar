#!/usr/bin/env python3
"""Verify that CLI version is sourced from package metadata."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "orchestrator" / "pyproject.toml"
CLI_PATH = REPO_ROOT / "orchestrator" / "src" / "raidar" / "cli.py"


def _current_project_version() -> str:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"Missing [project].version in {PYPROJECT_PATH}")
    return version


def _check_cli_version_option() -> None:
    module = ast.parse(CLI_PATH.read_text(encoding="utf-8"), filename=str(CLI_PATH))
    main_fn = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        ),
        None,
    )
    if main_fn is None:
        raise ValueError(f"Could not locate main() in {CLI_PATH}")

    version_option = next(
        (
            deco
            for deco in main_fn.decorator_list
            if isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr == "version_option"
        ),
        None,
    )
    if version_option is None:
        raise ValueError(f"main() is missing click.version_option decorator in {CLI_PATH}")

    version_kw = next((kw for kw in version_option.keywords if kw.arg == "version"), None)
    if version_kw is not None:
        raise ValueError("click.version_option must not use a hardcoded `version=` value")

    package_kw = next((kw for kw in version_option.keywords if kw.arg == "package_name"), None)
    if package_kw is None:
        raise ValueError("click.version_option must define `package_name=\"raidar\"`")
    if not isinstance(package_kw.value, ast.Constant) or package_kw.value.value != "raidar":
        raise ValueError("click.version_option package_name must be exactly \"raidar\"")


def main() -> int:
    version = _current_project_version()
    _check_cli_version_option()
    print(f"Version wiring check passed (project version: {version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
