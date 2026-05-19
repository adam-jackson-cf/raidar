"""Environment and Harbor operational CLI commands."""

from __future__ import annotations

import os
import subprocess

import click

from raidar.commands.shared import (
    ORCHESTRATOR_ROOT,
    cleanup_stale_harbor_before_runs,
    run_or_raise,
)
from raidar.runtime.maintenance import (
    cleanup_stale_harbor_resources,
    docker_compose_preflight_reason,
)


def register(main: click.Group) -> None:
    main.add_command(harbor)
    main.add_command(env)


@click.group()
def harbor() -> None:
    """Harbor operational commands."""


@harbor.command("cleanup")
@click.option(
    "--include-containers/--no-include-containers",
    default=True,
    help="Remove stale stopped Harbor containers.",
)
@click.option(
    "--include-build-processes/--no-include-build-processes",
    default=True,
    help="Terminate stale orphan build processes.",
)
def harbor_cleanup(include_containers: bool, include_build_processes: bool) -> None:
    """Cleanup stale Harbor processes and containers."""
    cleanup_stale_harbor_resources(
        include_containers=include_containers,
        include_build_processes=include_build_processes,
    )
    click.echo("Harbor cleanup completed.")


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
    cleanup_stale_harbor_before_runs()

    reason = docker_compose_preflight_reason(dict(os.environ))
    if reason:
        raise click.ClickException(reason)

    if install_tools:
        run_or_raise(["uv", "python", "install", "3.12"], ORCHESTRATOR_ROOT)
        run_or_raise(["uv", "sync", "--frozen", *sync_arg], ORCHESTRATOR_ROOT)
        run_or_raise(["uv", "tool", "install", "harbor"], ORCHESTRATOR_ROOT)

    result = subprocess.run(["harbor", "--version"], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        click.echo(result.stdout.strip())
    click.echo("Environment setup completed.")
