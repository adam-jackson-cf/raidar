#!/usr/bin/env python3
"""Apply production guardrail decisions to isolated, declarative fixture inputs."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import click

ROOT = Path(__file__).resolve().parents[2]


def load_checker(path: Path) -> ModuleType:
    """Load the real checker without invoking its repository-wide CLI."""
    name = path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load checker {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def inspect_path_fixture(
    rule: str, checker: ModuleType, root: Path, fixture: dict[str, Any]
) -> list[str]:
    """Exercise repository placement and changed-test decisions."""
    path = fixture.get("path", "orchestrator/src/raidar/engine/canary.py")
    tree = ast.parse(fixture.get("source", ""))
    if rule == "STRUCT.dependency-direction":
        return cast("list[str]", checker.source_edge_findings(root, path))
    if rule == "STRUCT.folder-placement":
        return cast("list[str]", checker.path_findings(root, [path]))
    if rule == "NAME.conventions":
        return cast("list[str]", checker.symbol_findings(path, tree))
    if rule in {"TEST.placement", "TEST.tiers"}:
        return cast("list[str]", checker.tier_findings(path, tree))
    return cast(
        "list[str]",
        checker.evidence_findings(
            base_sources=fixture["base_sources"],
            current_sources=fixture["current_sources"],
            changed_source_paths=frozenset(fixture["changed_source_paths"]),
            behavior_map=fixture["behavior_map"],
            map_is_tracked=True,
        ),
    )


def inspect_python_fixture(
    rule: str, checker: ModuleType, root: Path, fixture: dict[str, Any]
) -> list[str]:
    """Exercise source shape, naming, decoding and analyzer boundaries."""
    path = fixture.get("path", "orchestrator/src/raidar/engine/canary.py")
    if rule in {"PYS000", "PYS251", "PYS252", "PYS253"}:
        return cast("list[str]", checker.analyzer_findings(root, fixture.get("paths", [path])))
    if rule == "PYQ299":
        return [str(item) for item in checker.class_interface_diagnostics(root, [root / path])]
    if rule.startswith("PYQ1"):
        return [str(item) for item in checker.structure_diagnostics_for_relpath(path)]
    source = fixture.get("source", "")
    diagnostics = checker.class_interface_diagnostics_for_source(source, path)
    diagnostics.extend(checker.scalability_diagnostics_for_source(source, path))
    return [str(item) for item in diagnostics]


def run_fanout_checker(checker: ModuleType, root: Path) -> list[str]:
    """Give the real fanout scanner a bounded, isolated git inventory."""
    subprocess.run(
        ["git", "init", "--quiet", str(root)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    results = checker.analyse_fanout(root)
    return [reason for result in results for reason in result.reasons]


def inspect_fixture(
    rule: str, checker: ModuleType, root: Path, fixture: dict[str, Any]
) -> list[str]:
    """Delegate each fixture to the same decision used by the repository gate."""
    if rule.startswith(("STRUCT.", "NAME.", "TEST.growth.", "TEST.duplicate-")) or rule in {
        "TEST.placement",
        "TEST.tiers",
    }:
        return inspect_path_fixture(rule, checker, root, fixture)
    if rule.startswith(("PYQ", "PYS")):
        return inspect_python_fixture(rule, checker, root, fixture)
    if rule.startswith("FANOUT."):
        return run_fanout_checker(checker, root)
    hygiene_checks = {
        "SEAM.evidence": "seam_findings",
        "SKILL.metadata": "skill_findings",
        "TEST.subprocess-timeouts": "subprocess_findings",
    }
    if rule in hygiene_checks:
        return cast("list[str]", getattr(checker, hygiene_checks[rule])(root))
    if rule == "TEST.coverage-minimum":
        findings = []
        try:
            checker.enforce_coverage_report(fixture["report"], checker.coverage_floors())
        except click.ClickException as error:
            findings.append(error.message)
        return findings
    raise ValueError(f"Unsupported bespoke canary rule: {rule}")


def main() -> int:
    """Materialize fixture bytes only in a throwaway root, then run real checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", required=True)
    parser.add_argument("--checker", type=Path, required=True)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    checker = load_checker(ROOT / args.checker)
    with tempfile.TemporaryDirectory(prefix="raidar-rule-canary-") as directory:
        root = Path(directory)
        for relative, text in fixture.get("files", {}).items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
        for relative, octets in fixture.get("bytes", {}).items():
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bytes(octets))
        findings = inspect_fixture(args.rule, checker, root, fixture)
    for finding in findings:
        print(finding)
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
