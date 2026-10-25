"""Remove trial Compose projects that a killed or crashed run left behind.

Every Raidar trial environment is a Harbor Compose project named
``<trial id>__env-<run scope>`` (``raidar.engine.trial.environment``), where the
run scope is a 10-hex token of the orchestrator host name and the job's
``trials/`` directory. Stopping
``raidar run`` mid-trial skips Harbor's teardown, so those projects' containers
and networks stay behind. Cleanup targets only projects with that shape, on the
Docker context of the selected target:

- with a job directory: every project of that run, running or not (the caller
  names the job, so its live containers are known to be orphans);
- without one: only projects whose containers are all terminal, so concurrent runs on
  the same Docker host are never touched.

Unrelated Compose projects (for example an observability stack) never match.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.engine.trial.environment import run_scope

TRIAL_PROJECT_PATTERN = re.compile(r"__env-(?P<scope>[0-9a-f]{10})$")
TERMINAL_STATES = frozenset({"exited", "dead"})
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
    active: bool


def trial_projects(listing: str) -> list[TrialProject]:
    """Raidar trial projects from ``docker ps -a`` lines of ``<project>\\t<state>``."""
    states: dict[str, bool] = {}
    for line in listing.splitlines():
        project, _, state = line.strip().partition("\t")
        if TRIAL_PROJECT_PATTERN.search(project):
            states[project] = states.get(project, False) or state not in TERMINAL_STATES
    return [
        TrialProject(name, match.group("scope"), active)
        for name, active in sorted(states.items())
        if (match := TRIAL_PROJECT_PATTERN.search(name))
    ]


def select_stale(projects: Sequence[TrialProject], job_dir: Path | None) -> list[TrialProject]:
    """The projects to remove: a named run's, or projects with only terminal containers."""
    if job_dir is not None:
        scope = run_scope(job_dir / "trials")
        return [project for project in projects if project.scope == scope]
    return [project for project in projects if not project.active]


def _docker(docker_context: str, *args: str) -> list[str]:
    return ["docker", "--context", docker_context, *args]


def _list_projects(docker_context: str, runner: Runner) -> list[TrialProject]:
    listed = runner(
        _docker(
            docker_context,
            "ps",
            "-a",
            "--filter",
            f"label={PROJECT_LABEL}",
            "--format",
            f'{{{{.Label "{PROJECT_LABEL}"}}}}\t{{{{.State}}}}',
        )
    )
    if listed.returncode != 0:
        raise RuntimeError(f"docker ps failed: {listed.stderr.strip()}")
    return trial_projects(listed.stdout)


def _reap_if_stale(
    project: TrialProject, *, require_terminal: bool, docker_context: str, runner: Runner
) -> bool:
    """Recheck an unrestricted prune candidate, then reap it; whether it was removed."""
    if require_terminal:
        terminal = select_stale(_list_projects(docker_context, runner), None)
        if project.name not in {p.name for p in terminal}:
            return False
    done = reap_project(project.name, docker_context=docker_context, runner=runner)
    if done["returncode"] != 0:
        raise RuntimeError(str(done["stderr"]))
    return True


def cleanup_trial_projects(
    job_dir: Path | None = None,
    *,
    docker_context: str,
    dry_run: bool = False,
    runner: Runner = _run,
) -> list[str]:
    """Remove stale trial projects (``docker compose down``) on ``docker_context``; their names.

    Every stale project is attempted; if any ``down`` fails, the others are still removed and a
    ``RuntimeError`` then names the ones left behind (a project is never reported removed when
    docker refused).
    """
    stale = select_stale(_list_projects(docker_context, runner), job_dir)
    if dry_run:
        return [project.name for project in stale]
    removed = []
    failed = []
    for project in stale:
        try:
            if _reap_if_stale(
                project,
                require_terminal=job_dir is None,
                docker_context=docker_context,
                runner=runner,
            ):
                removed.append(project.name)
        except (subprocess.SubprocessError, OSError, RuntimeError) as error:
            failed.append(f"{project.name}: {type(error).__name__}: {error}")
    if failed:
        raise RuntimeError("docker compose down failed for " + "; ".join(failed))
    return removed


def reap_project(project: str, *, docker_context: str, runner: Runner = _run) -> dict[str, object]:
    """Remove one trial's Compose project (containers, networks); what docker answered."""
    done = runner(_docker(docker_context, "compose", "-p", project, "down", "--remove-orphans"))
    return {"returncode": done.returncode, "stderr": done.stderr.strip()[-2000:]}
