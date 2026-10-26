"""``raidar releases``: check the agent and workflow release register."""

from __future__ import annotations

import click

from raidar.subject.releases import release_issues


def register(main: click.Group) -> None:
    main.add_command(releases_group)


@click.group("releases")
def releases_group() -> None:
    """Agent and workflow releases (lab/releases.yaml)."""


@releases_group.command("check")
def releases_check_command() -> None:
    """Fail unless every definition is released at its current hash and pins agree."""
    problems = release_issues()
    for problem in problems:
        click.echo(f"releases: {problem}", err=True)
    if problems:
        raise SystemExit(1)
    click.echo("releases: ok")
