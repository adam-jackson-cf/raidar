"""Functional scoring: build success and test pass/fail."""

import re
import subprocess
from pathlib import Path

from ..config import settings
from ..schemas.scorecard import FunctionalScore


def run_command(command: str, cwd: Path, timeout: int | None = None) -> tuple[int, str, str]:
    """Run a command and capture output."""
    if timeout is None:
        timeout = settings.timeouts.command_default

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"


def check_build(workspace: Path) -> bool:
    """Check if the build succeeds."""
    code, stdout, stderr = run_command("bun run build", workspace, timeout=settings.timeouts.build)
    return code == 0


def check_typecheck(workspace: Path) -> bool:
    """Check if typecheck passes."""
    code, stdout, stderr = run_command(
        "bun run typecheck", workspace, timeout=settings.timeouts.typecheck
    )
    return code == 0


def parse_test_output(stdout: str, stderr: str) -> tuple[int, int]:
    """Parse test output to extract pass/fail counts.

    Returns:
        Tuple of (tests_passed, tests_total)
    """
    output = stdout + stderr

    # Try bun test format: "X pass"
    pass_match = re.search(r"(\d+) pass", output)
    fail_match = re.search(r"(\d+) fail", output)

    passed = int(pass_match.group(1)) if pass_match else 0
    failed = int(fail_match.group(1)) if fail_match else 0

    return passed, passed + failed


def run_tests(workspace: Path) -> tuple[bool, int, int]:
    """Run tests and return results.

    Returns:
        Tuple of (all_passed, tests_passed, tests_total)
    """
    code, stdout, stderr = run_command("bun test", workspace, timeout=settings.timeouts.test)
    tests_passed, tests_total = parse_test_output(stdout, stderr)

    # If no tests found, consider it passed with 0/0
    if tests_total == 0:
        return code == 0, 0, 0

    all_passed = code == 0 and tests_passed == tests_total
    return all_passed, tests_passed, tests_total


def evaluate_functional(workspace: Path) -> FunctionalScore:
    """Evaluate functional correctness of the implementation.

    Args:
        workspace: Path to the workspace directory

    Returns:
        FunctionalScore with build and test results
    """
    build_succeeded = check_build(workspace)
    tests_passed_all, tests_passed, tests_total = run_tests(workspace)

    return FunctionalScore(
        passed=build_succeeded and tests_passed_all,
        tests_passed=tests_passed,
        tests_total=tests_total,
        build_succeeded=build_succeeded,
    )
