"""Stale trial-environment cleanup (``raidar harbor cleanup``, pilot friction C4)."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from raidar.engine.trial.cleanup import (
    cleanup_trial_projects,
    reap_project,
    trial_projects,
)
from raidar.engine.trial.environment import run_scope

CONTEXT = "remote-ctx"


def _listing(*rows: tuple[str, str]) -> str:
    return "".join(f"{project}\t{state}\n" for project, state in rows)


class FakeDocker:
    def __init__(
        self,
        listing: str,
        returncode: int = 0,
        down: tuple[int, str] | Exception = (0, ""),
    ) -> None:
        self.listing = listing
        self.returncode = returncode
        self.down = down
        self.calls: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        assert list(command[1:3]) == ["--context", CONTEXT], command
        command = [command[0], *command[3:]]
        self.calls.append(list(command))
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                list(command), self.returncode, stdout=self.listing, stderr="no daemon"
            )
        if isinstance(self.down, Exception):
            raise self.down
        return subprocess.CompletedProcess(
            list(command), self.down[0], stdout="", stderr=self.down[1]
        )

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

    assert [(p.name, p.scope, p.active) for p in projects] == [
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

    removed = cleanup_trial_projects(docker_context=CONTEXT, runner=docker)

    assert removed == ["b01-p0-guided__env-abcdefabcd"]
    assert docker.downs() == ["b01-p0-guided__env-abcdefabcd"]


@pytest.mark.parametrize(
    ("state", "removed"),
    [
        ("running", False),
        ("paused", False),
        ("restarting", False),
        ("created", False),
        ("removing", False),
        ("unknown", False),
        ("", False),
        ("exited", True),
        ("dead", True),
    ],
)
def test_pruning_requires_confirmed_terminal_containers(state: str, removed: bool) -> None:
    project = "b00-p0-baseline__env-0123456789"
    docker = FakeDocker(_listing((project, state), (project, "exited")))
    expected = [project] if removed else []
    assert cleanup_trial_projects(docker_context=CONTEXT, runner=docker) == expected
    assert docker.downs() == expected


def test_pruning_rechecks_liveness_before_deletion() -> None:
    project = "b00-p0-baseline__env-0123456789"

    class RestartedDocker(FakeDocker):
        def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            result = super().__call__(command)
            self.listing = _listing((project, "restarting"))
            return result

    docker = RestartedDocker(_listing((project, "exited")))
    assert cleanup_trial_projects(docker_context=CONTEXT, runner=docker) == []
    assert docker.downs() == []


def test_a_named_run_removes_its_projects_even_when_running(tmp_path: Path) -> None:
    scope = run_scope(tmp_path / "trials")
    other = "0" * 10 if scope != "0" * 10 else "1" * 10
    docker = FakeDocker(
        _listing(
            (f"b00-p0-baseline__env-{scope}", "running"),
            (f"b00-p0-guided__env-{other}", "exited"),
        )
    )

    removed = cleanup_trial_projects(tmp_path, docker_context=CONTEXT, runner=docker)

    assert removed == [f"b00-p0-baseline__env-{scope}"]
    assert docker.downs() == [f"b00-p0-baseline__env-{scope}"]


def test_dry_run_lists_without_removing() -> None:
    docker = FakeDocker(_listing(("b01-p0-guided__env-abcdefabcd", "exited")))

    assert cleanup_trial_projects(docker_context=CONTEXT, dry_run=True, runner=docker) == [
        "b01-p0-guided__env-abcdefabcd"
    ]
    assert docker.downs() == []


def test_a_failed_listing_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="docker ps failed: no daemon"):
        cleanup_trial_projects(docker_context=CONTEXT, runner=FakeDocker("", returncode=1))


def test_reaping_one_project_runs_compose_down_on_the_given_context() -> None:
    docker = FakeDocker("")

    outcome = reap_project("t0__env-abcdefabcd", docker_context=CONTEXT, runner=docker)

    assert docker.downs() == ["t0__env-abcdefabcd"]
    assert outcome == {"returncode": 0, "stderr": ""}


@pytest.mark.parametrize(
    "failure",
    [(1, "network in use"), subprocess.TimeoutExpired(["docker"], 60), OSError("transport lost")],
)
def test_cleanup_attempts_every_project_before_reporting_failures(
    failure: tuple[int, str] | Exception,
) -> None:
    docker = FakeDocker(
        _listing(
            ("b00-p0-baseline__env-0123456789", "exited"),
            ("b01-p0-guided__env-abcdefabcd", "exited"),
        ),
        down=failure,
    )

    with pytest.raises(RuntimeError, match="b00-p0-baseline__env-0123456789") as captured:
        cleanup_trial_projects(docker_context=CONTEXT, runner=docker)

    # every project is attempted before the failure is raised
    assert docker.downs() == ["b00-p0-baseline__env-0123456789", "b01-p0-guided__env-abcdefabcd"]
    assert "b01-p0-guided__env-abcdefabcd" in str(captured.value)
