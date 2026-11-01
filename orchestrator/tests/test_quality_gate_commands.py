import subprocess
from decimal import Decimal

import click
import pytest

from raidar.cli import process, quality

FLOOR = Decimal("95.00")


def report(*, line: tuple[int, int], branch: tuple[int, int]) -> dict:
    return {
        "totals": {
            "covered_lines": line[0],
            "num_statements": line[1],
            "covered_branches": branch[0],
            "num_branches": branch[1],
        }
    }


FLOORS = {"line": FLOOR, "branch": FLOOR}
# Of 100000 measured items: below, exactly at, above the 95% floor.
BELOW, EQUAL, ABOVE = 94_999, 95_000, 95_001
# 94.9996% displays as 95.00 at precision 2 yet is below the floor.
ROUNDS_UP_BELOW = (9_499_996, 10_000_000)
PASSING = (EQUAL, 100_000)


@pytest.mark.parametrize("kind", ["line", "branch"])
@pytest.mark.parametrize(
    ("covered", "total", "passes"),
    [
        (BELOW, 100_000, False),
        (*ROUNDS_UP_BELOW, False),
        (EQUAL, 100_000, True),
        (ABOVE, 100_000, True),
    ],
)
def test_coverage_gate_fails_below_the_floor_at_any_precision(
    kind: str, covered: int, total: int, passes: bool
) -> None:
    measured = {"line": PASSING, "branch": PASSING}
    measured[kind] = (covered, total)
    data = report(line=measured["line"], branch=measured["branch"])

    if passes:
        quality.enforce_coverage_report(data, FLOORS)
        return
    with pytest.raises(click.ClickException, match=f"{kind} coverage"):
        quality.enforce_coverage_report(data, FLOORS)


def test_line_and_branch_floors_are_enforced_independently() -> None:
    both_low = report(line=(BELOW, 100_000), branch=(BELOW, 100_000))
    with pytest.raises(click.ClickException) as raised:
        quality.enforce_coverage_report(both_low, FLOORS)
    assert "line coverage" in raised.value.message
    assert "branch coverage" in raised.value.message

    quality.enforce_coverage_report(
        report(line=(BELOW, 100_000), branch=(ABOVE, 100_000)),
        {"line": Decimal("94.00"), "branch": FLOOR},
    )


def test_coverage_gate_fails_when_nothing_was_measured() -> None:
    with pytest.raises(click.ClickException, match="no branch measurements"):
        quality.enforce_coverage_report(report(line=PASSING, branch=(0, 0)), FLOORS)


def test_quality_gate_helpers_validate_requirements_and_commands(monkeypatch) -> None:
    with pytest.raises(click.ClickException, match="--stage is only supported"):
        quality.validate_quality_gate_options(fix=False, stage=True)

    monkeypatch.setattr(quality.repo_state, "has_unstaged_changes", lambda _root: True)
    with pytest.raises(click.ClickException, match="Unstaged changes detected"):
        quality.validate_quality_gate_options(fix=True, stage=False)

    monkeypatch.setattr(
        quality.repo_state, "assert_no_generated_artifact_changes", lambda _root: None
    )
    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lizard" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lizard"):
        quality.assert_quality_gate_requirements()

    monkeypatch.setattr(
        quality.shutil, "which", lambda command: None if command == "lint-imports" else "/bin/tool"
    )
    with pytest.raises(click.ClickException, match="Missing required command: lint-imports"):
        quality.assert_quality_gate_requirements()


def test_a_failed_child_command_stops_the_quality_runner(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        process.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode=7),
    )
    with pytest.raises(click.ClickException, match="Command failed \\(7\\): tool fail"):
        process.run_or_raise(["tool", "fail"], tmp_path)
