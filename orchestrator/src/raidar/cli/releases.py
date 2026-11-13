"""``raidar releases``: check the agent and workflow release register."""

from __future__ import annotations

import click

from raidar.subject.release_check import release_base, release_issues
from raidar_contract.references import ReleaseError


def register(main: click.Group) -> None:
    main.add_command(releases_group)


@click.group("releases")
def releases_group() -> None:
    """Agent and workflow releases (lab/releases.yaml)."""


@releases_group.command("check")
@click.option(
    "--base",
    default=None,
    help="Commit whose registered versions must remain (default: merge base with main, "
    "or HEAD's parent on main).",
)
def releases_check_command(base: str | None) -> None:
    """Fail on stale register digests, pin drift or removed releases (re-hashing is allowed)."""
    try:
        resolved = base or release_base()
    except ReleaseError as exc:
        problems = [str(exc)]
    else:
        problems = release_issues(base=resolved)
    for problem in problems:
        click.echo(f"releases: {problem}", err=True)
    if problems:
        raise SystemExit(1)
    click.echo("releases: ok")
