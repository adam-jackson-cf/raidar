import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

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
PASSING = (EQUAL, 100_000)


@pytest.mark.parametrize("kind", ["line", "branch"])
@pytest.mark.parametrize(
    ("covered", "total", "passes"),
    [
        (BELOW, 100_000, False),
        # 94.9996% displays as 95.00 but remains below the floor.
        (9_499_996, 10_000_000, False),
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


@pytest.fixture
def quality_workspace(monkeypatch, tmp_path: Path) -> Path:
    """Keep generated coverage and the configured floors local to each command."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.raidar.coverage]\nline_floor = 95.00\nbranch_floor = 96.00\n")
    monkeypatch.setattr(quality, "PYPROJECT", pyproject)
    monkeypatch.setattr(quality, "tmp_root", lambda: tmp_path)
    monkeypatch.setattr(quality.repo_state, "has_unstaged_changes", lambda _root: False)
    monkeypatch.setattr(
        quality.repo_state, "assert_no_generated_artifact_changes", lambda _root: None
    )
    monkeypatch.setattr(quality.shutil, "which", lambda _command: "/bin/tool")
    return tmp_path


@pytest.fixture
def quality_children(monkeypatch):
    """Model tool outcomes without launching children or changing tracked files."""

    def install(*, measurements=None, failure: str | None = None):
        state = {"staged": False, "tested": False}

        def run(command, **kwargs):
            if failure is not None and failure in command:
                return subprocess.CompletedProcess(command, returncode=7)
            if command[0] == "git":
                state["staged"] = True
            elif command[1:3] == ["-m", "pytest"]:
                state["tested"] = True
                destination = Path(kwargs["env"]["COVERAGE_FILE"]).with_name("coverage.json")
                destination.write_text(
                    json.dumps(
                        measurements
                        if measurements is not None
                        else report(line=(100, 100), branch=(100, 100))
                    )
                )
            return subprocess.CompletedProcess(command, returncode=0)

        monkeypatch.setattr(process.subprocess, "run", run)
        return state

    return install


@pytest.mark.parametrize(
    ("options", "staged"),
    [([], False), (["--fix"], False), (["--fix", "--stage"], True)],
)
def test_quality_gates_stage_only_after_success_when_explicitly_requested(
    quality_workspace, quality_children, options, staged: bool
) -> None:
    state = quality_children()

    result = CliRunner().invoke(quality.quality, ["gates", *options])

    assert result.exit_code == 0, result.output
    assert "[quality-gates] Completed successfully" in result.output
    assert state == {"staged": staged, "tested": True}
    assert (quality_workspace / "tests" / "coverage" / "coverage.json").is_file()


@pytest.mark.parametrize(
    "failed_tool",
    [
        "format",
        "check",
        str(quality.FANOUT_CHECK_SCRIPT),
        str(quality.PYTHON_QUALITY_CHECK_SCRIPT),
        "lint-imports",
        "lizard",
        str(quality.MYPY_CHECK_SCRIPT),
        "pytest",
    ],
)
def test_quality_gates_stop_on_a_failed_tool_without_staging(
    quality_workspace, quality_children, failed_tool: str
) -> None:
    state = quality_children(failure=failed_tool)

    result = CliRunner().invoke(quality.quality, ["gates", "--fix", "--stage"])

    assert result.exit_code == 1
    assert "Command failed (7)" in result.output
    assert failed_tool in result.output
    assert "[quality-gates] Completed successfully" not in result.output
    assert not state["staged"]
    assert not state["tested"]


@pytest.mark.parametrize(
    ("options", "unstaged", "message"),
    [
        (["--stage"], False, "--stage is only supported"),
        (["--fix", "--stage"], True, "Unstaged changes detected"),
    ],
)
def test_quality_gates_refuse_unsafe_options_before_running_tools(
    monkeypatch, quality_workspace, quality_children, options, unstaged: bool, message: str
) -> None:
    monkeypatch.setattr(quality.repo_state, "has_unstaged_changes", lambda _root: unstaged)
    state = quality_children()

    result = CliRunner().invoke(quality.quality, ["gates", *options])

    assert result.exit_code == 1
    assert message in result.output
    assert "[exec]" not in result.output
    assert not any(state.values())


@pytest.mark.parametrize(
    ("measurements", "exit_code", "message"),
    [
        (report(line=(95, 100), branch=(96, 100)), 0, None),
        (report(line=(94, 100), branch=(96, 100)), 1, "line coverage 94/100"),
        (report(line=(95, 100), branch=(95, 100)), 1, "branch coverage 95/100"),
        ({"totals": {"covered_lines": 95, "num_statements": 100}}, 1, "no branch measurements"),
    ],
)
def test_fast_command_enforces_configured_floors_from_the_generated_report(
    quality_workspace, quality_children, measurements, exit_code: int, message: str | None
) -> None:
    state = quality_children(measurements=measurements)

    result = CliRunner().invoke(quality.quality, ["test-fast"])

    assert result.exit_code == exit_code, result.output
    assert state["tested"]
    if message is not None:
        assert message in result.output


def test_insufficient_coverage_prevents_staging_and_success(
    quality_workspace, quality_children
) -> None:
    state = quality_children(measurements=report(line=(94, 100), branch=(95, 100)))

    result = CliRunner().invoke(quality.quality, ["gates", "--fix", "--stage"])

    assert result.exit_code == 1
    assert "line coverage 94/100" in result.output
    assert "branch coverage 95/100" in result.output
    assert not state["staged"]
    assert "[quality-gates] Completed successfully" not in result.output


def test_fast_command_preserves_the_callers_coverage_artifacts(
    monkeypatch, quality_workspace, quality_children
) -> None:
    caller_report = quality_workspace / "coverage.json"
    caller_report.write_text('{"caller": true}')
    caller_file = str(quality_workspace / ".coverage")
    monkeypatch.setenv("COVERAGE_FILE", caller_file)
    quality_children()

    result = CliRunner().invoke(quality.quality, ["test-fast"])

    assert result.exit_code == 0, result.output
    assert caller_report.read_text() == '{"caller": true}'
    assert os.environ["COVERAGE_FILE"] == caller_file
    generated = quality_workspace / "tests" / "coverage" / "coverage.json"
    assert json.loads(generated.read_text()) == report(line=(100, 100), branch=(100, 100))
