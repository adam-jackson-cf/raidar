"""Environment setup and Harbor operational CLI commands."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import click

from raidar.cli.process import run_or_raise
from raidar.cli.target_options import docker_selection, target_option
from raidar.contract.paths import ORCHESTRATOR_ROOT
from raidar.engine.trial.cleanup import cleanup_trial_projects
from raidar.subject.agent.adapters.base import harbor_executable

COMPOSE_MINIMUM_VERSION = (2, 40, 1)
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def register(main: click.Group) -> None:
    main.add_command(harbor)
    main.add_command(env)


def compose_version(output: str) -> tuple[int, int, int] | None:
    match = _VERSION.search(output)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def compose_preflight_reason() -> str | None:
    """Why this Docker Compose cannot run Harbor trials, or None when it can."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    version = compose_version(result.stdout) if result.returncode == 0 else None
    if version is None or version >= COMPOSE_MINIMUM_VERSION:
        return None
    required = ".".join(map(str, COMPOSE_MINIMUM_VERSION))
    detected = ".".join(map(str, version))
    return f"Unsupported docker compose version {detected}. Require >= {required} for Harbor runs."


@click.group()
def harbor() -> None:
    """Harbor operational commands."""


@harbor.command("cleanup")
@click.option(
    "--job",
    "job_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Remove every trial environment of this job directory, running or not.",
)
@click.option("--dry-run", is_flag=True, help="List the trial environments without removing.")
@target_option
def harbor_cleanup(job_dir: Path | None, dry_run: bool, target_name: str | None) -> None:
    """Remove trial environments a stopped `raidar run` left behind.

    Without --job, only trial environments with no running container are removed,
    so a concurrent job on the same Docker host is never touched.
    """
    selection = docker_selection(target_name, ensure=False)
    try:
        removed = cleanup_trial_projects(
            job_dir, docker_context=selection.trial.docker_context, dry_run=dry_run
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(str(exc)) from exc
    verb = "would remove" if dry_run else "removed"
    for project in removed:
        click.echo(f"{verb}: {project}")
    click.echo(f"Harbor cleanup completed ({len(removed)} trial environment(s) {verb}).")


@click.group()
def env() -> None:
    """Environment setup and diagnostics."""


@env.command("setup")
@click.option(
    "--install-tools/--no-install-tools",
    default=True,
    help="Install required toolchain components with uv.",
)
@click.option(
    "--sync-arg",
    multiple=True,
    help="Additional argument to append to the frozen `uv sync` invocation.",
)
def env_setup(install_tools: bool, sync_arg: tuple[str, ...]) -> None:
    """Setup local toolchain and run Harbor preflight checks."""
    reason = compose_preflight_reason()
    if reason:
        raise click.ClickException(reason)

    if install_tools:
        run_or_raise(["uv", "python", "install", "3.12"], ORCHESTRATOR_ROOT)
        # Harbor is an exact-pinned project dependency; `uv sync` installs it into
        # the orchestrator venv, so no global `uv tool install harbor` is used.
        run_or_raise(["uv", "sync", "--frozen", *sync_arg], ORCHESTRATOR_ROOT)

    try:
        harbor = harbor_executable()
    except FileNotFoundError as error:
        raise click.ClickException(f"{error}; run `make env-setup` to sync it.") from error
    result = subprocess.run([harbor, "--version"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(
            "Project-pinned Harbor CLI is unavailable; run `make env-setup` to sync it."
        )
    click.echo(f"harbor {result.stdout.strip()}")
    click.echo("Environment setup completed.")
