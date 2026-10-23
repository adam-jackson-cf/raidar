"""``raidar target``: inspect, start, stop and prune execution targets (mechanics-v1 §3.1)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import click

from raidar.cli.target_options import target_option
from raidar.execution.cleanup import cleanup_trial_projects
from raidar.targets.config import (
    TARGETS_TEMPLATE,
    Target,
    TargetError,
    load_targets,
    resolve_target,
    targets_file,
)
from raidar.targets.health import (
    TargetUnavailableError,
    data_check_failure,
    docker_facts,
    ensure_target,
)
from raidar.targets.transport import (
    TransportPaths,
    docker_healthy,
    read_pid,
    transport_down,
    transport_up,
    tunnel_running,
)


def register(main: click.Group) -> None:
    main.add_command(target)


def _echo_json(payload: object) -> None:
    click.echo(json.dumps(payload, indent=2))


def _selected(target_name: str | None) -> Target:
    try:
        return resolve_target(target_name)
    except TargetError as error:
        raise click.ClickException(str(error)) from error


def _transport_facts(target: Target) -> dict[str, Any] | None:
    if target.transport is None:
        return None
    paths = TransportPaths.of(target)
    return {
        "kind": "ssh-socket",
        "host": target.transport.host,
        "pid": read_pid(paths),
        "alive": tunnel_running(paths),
        "socket": str(paths.socket),
        "log": str(paths.log),
    }


def _summary(target: Target) -> dict[str, Any]:
    return {
        "name": target.name,
        "kind": target.kind,
        "docker_context": target.docker_context,
        "trusted": target.trusted,
        "max_concurrency": target.max_concurrency,
        "grader": target.grader,
        "transport": _transport_facts(target),
    }


@click.group()
def target() -> None:
    """Execution targets: the Docker engine that runs trials and graders."""


@target.command("list")
def target_list() -> None:
    """List the defined targets and the one currently selected."""
    try:
        book = load_targets()
        selected = resolve_target(None, book=book).name
    except TargetError as error:
        raise click.ClickException(str(error)) from error
    _echo_json(
        {
            "file": str(book.source or targets_file()),
            "default": book.default,
            "selected": selected,
            "targets": [_summary(item) for item in book.targets.values()],
        }
    )


@target.command("status")
@target_option
def target_status(target_name: str | None) -> None:
    """Facts of the selected target; non-zero exit when it is unreachable or fails its checks."""
    chosen = _selected(target_name)
    facts = docker_facts(chosen)
    report: dict[str, Any] = _summary(chosen) | {
        "reachable": facts is not None,
        "docker": facts.as_record() if facts else None,
        "healthy": False,
    }
    failure = (
        data_check_failure(chosen)
        if facts is not None
        else f"docker context {chosen.docker_context!r} is unreachable"
    )
    report["healthy"] = failure is None
    report["problem"] = failure
    _echo_json(report)
    if failure is not None:
        raise click.exceptions.Exit(1)


@target.command("up")
@target_option
def target_up(target_name: str | None) -> None:
    """Start the target's transport (SSH forward) and its Docker context."""
    chosen = _selected(target_name)
    if chosen.transport is None:
        raise click.ClickException(f"target {chosen.name!r} has no transport to start")
    try:
        pid = transport_up(chosen)
    except (RuntimeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"target {chosen.name}: up (ssh pid {pid}, context {chosen.docker_context})")


@target.command("down")
@target_option
def target_down(target_name: str | None) -> None:
    """Stop the target's transport; its Docker context is kept."""
    chosen = _selected(target_name)
    if chosen.transport is None:
        raise click.ClickException(f"target {chosen.name!r} has no transport to stop")
    stopped = transport_down(chosen)
    click.echo(f"target {chosen.name}: {'stopped' if stopped else 'was not running'}")


@target.command("ensure")
@target_option
def target_ensure(target_name: str | None) -> None:
    """Make the target reachable (restarting its transport if needed) and run its data check."""
    chosen = _selected(target_name)
    try:
        ensure_target(chosen)
    except TargetUnavailableError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"target {chosen.name}: ok (context {chosen.docker_context})")


@target.command("init")
def target_init() -> None:
    """Write a commented targets file template; never overwrites."""
    path = targets_file()
    if path.exists():
        raise click.ClickException(f"{path} already exists; edit it instead")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TARGETS_TEMPLATE)
    click.echo(f"wrote {path}")


@target.command("prune")
@target_option
@click.option("--dry-run", is_flag=True, help="List the stale trial environments without removing.")
def target_prune(target_name: str | None, dry_run: bool) -> None:
    """Remove stale Raidar trial Compose projects (nothing running) on the target."""
    chosen = _selected(target_name)
    if not docker_healthy(chosen.docker_context):
        raise click.ClickException(
            f"target {chosen.name!r} is unreachable; run `raidar target ensure` first"
        )
    try:
        removed = cleanup_trial_projects(
            None, docker_context=chosen.docker_context, dry_run=dry_run
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise click.ClickException(str(error)) from error
    verb = "would remove" if dry_run else "removed"
    for project in removed:
        click.echo(f"{verb}: {project}")
    click.echo(f"target {chosen.name}: {len(removed)} stale trial environment(s) {verb}.")
