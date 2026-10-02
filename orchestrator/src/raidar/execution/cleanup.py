"""Remove trial Compose projects that a killed or crashed run left behind.

Every Raidar trial environment is a Harbor Compose project named
``<trial id>__env-<run scope>`` (``raidar.execution.environment``), where the
run scope is a 10-hex token of the job's ``trials/`` directory. Stopping
``raidar run`` mid-trial skips Harbor's teardown, so those projects' containers
and networks stay behind. Cleanup targets only projects with that shape:

- with a job directory: every project of that run, running or not (the caller
  names the job, so its live containers are known to be orphans);
- without one: only projects with no running container, so concurrent runs on
  the same Docker host are never touched.

Unrelated Compose projects (for example an observability stack) never match.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.execution.environment import run_scope

TRIAL_PROJECT_PATTERN = re.compile(r"__env-(?P<scope>[0-9a-f]{10})$")
DOCKER_TIMEOUT_S = 60
PROJECT_LABEL = "com.docker.compose.project"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S, check=False
    )


@dataclass(frozen=True)
class TrialProject:
    name: str
    scope: str
    running: bool


def trial_projects(listing: str) -> list[TrialProject]:
    """Raidar trial projects from ``docker ps -a`` lines of ``<project>\\t<state>``."""
    states: dict[str, bool] = {}
    for line in listing.splitlines():
        project, _, state = line.strip().partition("\t")
        if TRIAL_PROJECT_PATTERN.search(project):
            states[project] = states.get(project, False) or state == "running"
    return [
        TrialProject(name, match.group("scope"), running)
        for name, running in sorted(states.items())
        if (match := TRIAL_PROJECT_PATTERN.search(name))
    ]


def select_stale(projects: Sequence[TrialProject], job_dir: Path | None) -> list[TrialProject]:
    """The projects to remove: a named run's, or every project with nothing running."""
    if job_dir is not None:
        scope = run_scope(job_dir / "trials")
        return [project for project in projects if project.scope == scope]
    return [project for project in projects if not project.running]


def cleanup_trial_projects(
    job_dir: Path | None = None, *, dry_run: bool = False, runner: Runner = _run
) -> list[str]:
    """Remove stale trial projects (``docker compose down``); return their names."""
    listed = runner(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={PROJECT_LABEL}",
            "--format",
            f'{{{{.Label "{PROJECT_LABEL}"}}}}\t{{{{.State}}}}',
        ]
    )
    if listed.returncode != 0:
        raise RuntimeError(f"docker ps failed: {listed.stderr.strip()}")
    stale = select_stale(trial_projects(listed.stdout), job_dir)
    if not dry_run:
        for project in stale:
            runner(["docker", "compose", "-p", project.name, "down", "--remove-orphans"])
    return [project.name for project in stale]
