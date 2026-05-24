"""Quality gate CLI commands."""

from __future__ import annotations

import os
import shutil

import click

from raidar.application import repo_state
from raidar.commands.shared import ORCHESTRATOR_ROOT, REPO_ROOT, python_cmd, run_or_raise

INTEGRATION_TEST_TARGETS = [
    "tests/test_harbor_env.py",
    "tests/test_harbor_cleanup_preflight.py",
    "tests/test_harbor_execution_phase.py",
    "tests/test_starter_preflight_cache.py",
    "tests/test_task_images_and_workspace_phase.py",
]
FANOUT_CHECK_SCRIPT = REPO_ROOT / "scripts" / "checks" / "check-python-fanout.py"
IMPORT_LINTER_CONFIG = ORCHESTRATOR_ROOT / ".importlinter"
TYPECHECK_TARGETS = [
    "src/raidar/watcher",
    "src/raidar/agents/adapters",
    "tests/test_codex_cli_adapter.py",
    "tests/test_claude_code_cli_adapter.py",
    "tests/test_gemini_cli_adapter.py",
]
COVERAGE_FAIL_UNDER = "95"


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
    if fix:
        run_or_raise([python_cmd(), "-m", "ruff", "format", "--force-exclude"], ORCHESTRATOR_ROOT)
        run_or_raise(
            [python_cmd(), "-m", "ruff", "check", ".", "--fix", "--force-exclude"],
            ORCHESTRATOR_ROOT,
        )
        return

    run_or_raise(
        [python_cmd(), "-m", "ruff", "format", "--check", "--force-exclude"],
        ORCHESTRATOR_ROOT,
    )
    run_or_raise(
        [python_cmd(), "-m", "ruff", "check", ".", "--no-fix", "--force-exclude"],
        ORCHESTRATOR_ROOT,
    )


def coverage_env() -> dict[str, str]:
    coverage_dir = ORCHESTRATOR_ROOT / ".pytest_cache" / "coverage"
    coverage_dir.mkdir(parents=True, exist_ok=True)
    coverage_env = dict(os.environ)
    coverage_env["COVERAGE_FILE"] = str(coverage_dir / ".coverage")
    return coverage_env


def run_orchestrator_quality_gates() -> None:
    run_or_raise(
        [python_cmd(), str(FANOUT_CHECK_SCRIPT), "--repo-root", str(REPO_ROOT)],
        REPO_ROOT,
    )
    run_or_raise(["lint-imports", "--config", str(IMPORT_LINTER_CONFIG)], ORCHESTRATOR_ROOT)
    run_or_raise(["lizard", "-C", "10", "-l", "python", "src"], ORCHESTRATOR_ROOT)
    run_or_raise(
        [python_cmd(), "-m", "mypy", "--follow-imports=skip", *TYPECHECK_TARGETS],
        ORCHESTRATOR_ROOT,
    )
    run_or_raise([python_cmd(), "-m", "pytest", "tests", "-x", "--tb=short"], ORCHESTRATOR_ROOT)
    run_or_raise(
        [python_cmd(), "-m", "pytest", *INTEGRATION_TEST_TARGETS, "-x", "--tb=short"],
        ORCHESTRATOR_ROOT,
    )
    run_or_raise(
        [
            python_cmd(),
            "-m",
            "pytest",
            "tests",
            "--cov=src",
            "--cov-report=term-missing:skip-covered",
            f"--cov-fail-under={COVERAGE_FAIL_UNDER}",
            "-x",
            "--tb=short",
        ],
        ORCHESTRATOR_ROOT,
        env=coverage_env(),
    )


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
