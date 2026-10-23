"""Shared ``--target`` option and the resolve/trust/ensure/activate step of Docker commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import click

from raidar.execution.cleanup import reap_project
from raidar.execution.environment import trial_project_name
from raidar.targets.config import (
    Selection,
    TargetError,
    activate,
    require_trusted,
    resolve_selection,
)
from raidar.targets.guard import TargetGuard, TargetLostError
from raidar.targets.health import docker_facts, ensure_target
from raidar.targets.job import JobTarget


def target_option(command: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--target",
        "target_name",
        default=None,
        help="Execution target (default: $RAIDAR_TARGET, then targets.toml `default`, "
        "then the current Docker context).",
    )(command)


def docker_selection(
    target_name: str | None,
    *,
    trial_purpose: str | None = None,
    grader_purpose: str | None = None,
    ensure: bool = True,
) -> Selection:
    """Resolve the target (lazily, only commands that use Docker call this).

    ``trial_purpose`` / ``grader_purpose`` name what the trial / grader target is
    trusted with (agent credentials, hidden tests); an untrusted target is refused.
    Both targets are ensured reachable, and the trial target becomes this
    process's Docker target (``DOCKER_CONTEXT``).
    """
    try:
        selection = resolve_selection(target_name)
        if trial_purpose:
            require_trusted(selection.trial, trial_purpose)
        if grader_purpose:
            require_trusted(selection.grader, grader_purpose)
        if ensure:
            ensure_target(selection.trial)
            if selection.grader is not selection.trial:
                ensure_target(selection.grader)
    except TargetError as error:
        raise click.ClickException(str(error)) from error
    activate(selection.trial)
    return selection


def job_target(target_name: str | None) -> JobTarget:
    """Resolve and ensure a run's targets; both must be trusted with credentials/hidden tests."""
    selection = docker_selection(
        target_name,
        trial_purpose="trials (agent credentials and hidden tests)",
        grader_purpose="hidden-test grading",
    )
    context = selection.trial.docker_context

    def reap(trial_id: str, trials_dir: Path) -> dict[str, Any]:
        project = trial_project_name(trial_id, trials_dir)
        return {"project": project, **reap_project(project, docker_context=context)}

    guard = TargetGuard.for_target(selection.trial, reap=reap)
    return JobTarget(selection, docker_facts(selection.trial), guard)


def run_guarded[T](job_dir: Path, job: Awaitable[T]) -> T:
    """Run a job; a target lost past the reconnect budget aborts it with a clear error."""

    async def run() -> T:
        return await job

    try:
        return asyncio.run(run())
    except TargetLostError as error:
        raise click.ClickException(f"job {job_dir} aborted: {error}") from error
