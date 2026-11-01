"""Quality gate CLI commands."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import click

from raidar.cli import repo_state
from raidar.cli.process import run_or_raise
from raidar.contract.paths import ORCHESTRATOR_ROOT, REPO_ROOT, tmp_root

PYTHON = sys.executable

FANOUT_CHECK_SCRIPT = REPO_ROOT / "scripts" / "checks" / "check-python-fanout.py"
PYTHON_QUALITY_CHECK_SCRIPT = REPO_ROOT / "scripts" / "checks" / "check-python-quality.py"
IMPORT_LINTER_CONFIG = ORCHESTRATOR_ROOT / ".importlinter"
TYPECHECK_TARGETS = [
    "src/raidar",
    "../contract/src",
    "tests/test_codex_cli_adapter.py",
    "tests/test_claude_code_cli_adapter.py",
    "tests/at3_support.py",
]
FAST_SELECTOR = "not slow and not quality and not docker and not security and not perf"
PYPROJECT = ORCHESTRATOR_ROOT / "pyproject.toml"
COVERAGE_KINDS = ("line", "branch")


def package_tool_sources() -> list[str]:
    """Stage-owned Python source roots, relative to the orchestrator."""
    return sorted(
        path.relative_to(ORCHESTRATOR_ROOT, walk_up=True).as_posix()
        for path in (
            manifest.parent / "tool" / "src"
            for manifest in REPO_ROOT.glob("lab/stages/*/*/stage.yaml")
        )
        if path.is_dir()
    )


def register(main: click.Group) -> None:
    main.add_command(quality)


@click.group()
def quality() -> None:
    """Quality gate commands."""


def validate_quality_gate_options(*, fix: bool, stage: bool) -> None:
    if stage and not fix:
        raise click.ClickException("--stage is only supported together with --fix.")
    if fix and repo_state.has_unstaged_changes(REPO_ROOT):
        raise click.ClickException(
            "Unstaged changes detected. Stage or stash before running --fix."
        )


def assert_quality_gate_requirements() -> None:
    repo_state.assert_no_generated_artifact_changes(REPO_ROOT)
    if shutil.which("lizard") is None:
        raise click.ClickException("Missing required command: lizard")
    if shutil.which("lint-imports") is None:
        raise click.ClickException("Missing required command: lint-imports")


def run_ruff_quality_gates(*, fix: bool) -> None:
    targets = [".", "../contract/src", *package_tool_sources()]
    options = ["--config", "pyproject.toml", "--force-exclude"]
    if fix:
        run_or_raise([PYTHON, "-m", "ruff", "format", *options, *targets], ORCHESTRATOR_ROOT)
        run_or_raise(
            [PYTHON, "-m", "ruff", "check", *targets, "--fix", *options], ORCHESTRATOR_ROOT
        )
        return

    run_or_raise([PYTHON, "-m", "ruff", "format", "--check", *options, *targets], ORCHESTRATOR_ROOT)
    run_or_raise([PYTHON, "-m", "ruff", "check", *targets, "--no-fix", *options], ORCHESTRATOR_ROOT)


def coverage_env() -> dict[str, str]:
    coverage_dir = tmp_root() / "tests" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage_env = dict(os.environ)
    coverage_env["COVERAGE_FILE"] = str(coverage_dir / ".coverage")
    return coverage_env


def coverage_floors() -> dict[str, Decimal]:
    """The line and branch floors, in percent, from the one config truth."""
    with PYPROJECT.open("rb") as handle:
        section = tomllib.load(handle)["tool"]["raidar"]["coverage"]
    return {kind: Decimal(str(section[f"{kind}_floor"])) for kind in COVERAGE_KINDS}


def assert_coverage_floor(kind: str, covered: int, total: int, floor: Decimal) -> None:
    """Fail when covered/total is below the floor, at any precision.

    Exact integer comparison: covered / total >= floor / 100 without rounding, so a value
    that only displays as the floor (94.996 -> 95.00) still fails.
    """
    if total <= 0:
        raise click.ClickException(f"Coverage gate: no {kind} measurements recorded.")
    numerator, denominator = floor.as_integer_ratio()
    if covered * 100 * denominator < numerator * total:
        raise click.ClickException(
            f"Coverage gate: {kind} coverage {covered}/{total} is below the {floor}% floor."
        )


def enforce_coverage_report(report: dict[str, Any], floors: dict[str, Decimal]) -> None:
    """Enforce line and branch floors separately against a coverage JSON report."""
    totals = report["totals"]
    counts = {
        "line": (totals["covered_lines"], totals["num_statements"]),
        "branch": (totals.get("covered_branches", 0), totals.get("num_branches", 0)),
    }
    failures: list[str] = []
    for kind in COVERAGE_KINDS:
        try:
            assert_coverage_floor(kind, *counts[kind], floors[kind])
        except click.ClickException as error:
            failures.append(error.message)
    if failures:
        raise click.ClickException("\n".join(failures))


def run_fast_tier() -> None:
    """Run the fast tier with coverage over all production source and enforce the floors."""
    tools = package_tool_sources()
    env = coverage_env()
    report_path = Path(env["COVERAGE_FILE"]).with_name("coverage.json")
    run_or_raise(
        [
            PYTHON,
            "-m",
            "pytest",
            "tests",
            "-m",
            FAST_SELECTOR,
            f"--cov-config={PYPROJECT}",
            "--cov=src",
            "--cov=../contract/src",
            *(f"--cov={source}" for source in tools),
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=json:{report_path}",
            "--durations=10",
            "-x",
            "--tb=short",
        ],
        ORCHESTRATOR_ROOT,
        env=env,
    )
    enforce_coverage_report(json.loads(report_path.read_text()), coverage_floors())


def run_orchestrator_quality_gates() -> None:
    tools = package_tool_sources()
    source_roots = [
        "orchestrator/src",
        "contract/src",
        *(
            (ORCHESTRATOR_ROOT / source).resolve().relative_to(REPO_ROOT).as_posix()
            for source in tools
        ),
    ]
    root_packages = [
        "raidar",
        "raidar_contract",
        *(
            init.parent.name
            for source in tools
            for init in (ORCHESTRATOR_ROOT / source).glob("*/__init__.py")
        ),
    ]
    run_or_raise(
        [
            PYTHON,
            str(FANOUT_CHECK_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            *(part for source in source_roots for part in ("--source-root", source)),
            *(part for name in root_packages for part in ("--root-package", name)),
        ],
        REPO_ROOT,
    )
    run_or_raise(
        [PYTHON, str(PYTHON_QUALITY_CHECK_SCRIPT), "--repo-root", str(REPO_ROOT)],
        REPO_ROOT,
    )
    run_or_raise(["lint-imports", "--config", str(IMPORT_LINTER_CONFIG)], ORCHESTRATOR_ROOT)
    run_or_raise(
        ["lizard", "-C", "10", "-l", "python", "src", "../contract/src", *tools], ORCHESTRATOR_ROOT
    )
    run_or_raise(
        [PYTHON, "-m", "mypy", "--follow-imports=skip", *TYPECHECK_TARGETS, *tools],
        ORCHESTRATOR_ROOT,
    )
    run_fast_tier()


@quality.command("gates")
@click.option("--fix", is_flag=True, help="Apply auto-fixes where supported.")
@click.option("--stage", is_flag=True, help="Stage tracked file updates after fixes.")
def quality_gates(fix: bool, stage: bool) -> None:
    """Run deterministic quality gates for orchestrator source."""
    validate_quality_gate_options(fix=fix, stage=stage)
    assert_quality_gate_requirements()
    run_ruff_quality_gates(fix=fix)
    run_orchestrator_quality_gates()

    if stage:
        run_or_raise(["git", "-C", str(REPO_ROOT), "add", "-u"], REPO_ROOT)

    click.echo("[quality-gates] Completed successfully")


@quality.command("test-fast")
def quality_test_fast() -> None:
    """Run the fast tier with coverage and enforce the line and branch floors."""
    run_fast_tier()
