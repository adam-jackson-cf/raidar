"""Stale trial-environment cleanup (``raidar harbor cleanup``, pilot friction C4)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from raidar.execution.cleanup import cleanup_trial_projects, select_stale, trial_projects
from raidar.execution.environment import run_scope


def _listing(*rows: tuple[str, str]) -> str:
    return "".join(f"{project}\t{state}\n" for project, state in rows)


class FakeDocker:
    def __init__(self, listing: str, returncode: int = 0) -> None:
        self.listing = listing
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                list(command), self.returncode, stdout=self.listing, stderr="no daemon"
            )
        return subprocess.CompletedProcess(list(command), 0, stdout="", stderr="")

    def downs(self) -> list[str]:
        return [call[3] for call in self.calls if call[:3] == ["docker", "compose", "-p"]]


def test_only_run_scoped_trial_projects_are_recognised() -> None:
    projects = trial_projects(
        _listing(
            ("signoz", "running"),
            ("b00-p0-baseline__env-0123456789", "exited"),
            ("b00-p0-baseline__env-0123456789", "running"),
            ("b01-p0-guided__env-abcdefabcd", "exited"),
            ("b02-p0-guided__env-short", "exited"),
            ("", "exited"),
        )
    )

    assert [(p.name, p.scope, p.running) for p in projects] == [
        ("b00-p0-baseline__env-0123456789", "0123456789", True),
        ("b01-p0-guided__env-abcdefabcd", "abcdefabcd", False),
    ]


def test_without_a_run_only_projects_with_nothing_running_are_removed() -> None:
    docker = FakeDocker(
        _listing(
            ("b00-p0-baseline__env-0123456789", "running"),
            ("b01-p0-guided__env-abcdefabcd", "exited"),
            ("signoz", "exited"),
        )
    )

    removed = cleanup_trial_projects(runner=docker)

    assert removed == ["b01-p0-guided__env-abcdefabcd"]
    assert docker.downs() == ["b01-p0-guided__env-abcdefabcd"]


def test_a_named_run_removes_its_projects_even_when_running(tmp_path: Path) -> None:
    scope = run_scope(tmp_path / "trials")
    other = "0" * 10 if scope != "0" * 10 else "1" * 10
    docker = FakeDocker(
        _listing(
            (f"b00-p0-baseline__env-{scope}", "running"),
            (f"b00-p0-guided__env-{other}", "exited"),
        )
    )

    removed = cleanup_trial_projects(tmp_path, runner=docker)

    assert removed == [f"b00-p0-baseline__env-{scope}"]
    assert docker.downs() == [f"b00-p0-baseline__env-{scope}"]


def test_dry_run_lists_without_removing() -> None:
    docker = FakeDocker(_listing(("b01-p0-guided__env-abcdefabcd", "exited")))

    assert cleanup_trial_projects(dry_run=True, runner=docker) == ["b01-p0-guided__env-abcdefabcd"]
    assert docker.downs() == []


def test_a_failed_listing_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="docker ps failed: no daemon"):
        cleanup_trial_projects(runner=FakeDocker("", returncode=1))


def test_select_stale_is_empty_without_trial_projects(tmp_path: Path) -> None:
    assert select_stale([], None) == []
    assert select_stale([], tmp_path) == []
