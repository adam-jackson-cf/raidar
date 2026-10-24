"""``raidar ledger``: check and index the tracked fault/remedy ledger."""

from __future__ import annotations

from pathlib import Path

import click

from raidar.contract.paths import lab_root
from raidar.evaluation.ledger import index
from raidar.evaluation.ledger.check import check_ledger, load_ledger

LEDGER_ROOT = lab_root() / "ledger"


def register(main: click.Group) -> None:
    main.add_command(ledger_group)


@click.group("ledger")
def ledger_group() -> None:
    """Fault categories (FCR) and remedies (M)."""


_ROOT_OPTION = click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=LEDGER_ROOT,
    show_default=True,
    help="Ledger directory.",
)


@ledger_group.command("check")
@_ROOT_OPTION
def ledger_check_command(root: Path) -> None:
    """Fail on schema, id, link, status-transition, date or index problems."""
    problems = check_ledger(root)
    for problem in problems:
        click.echo(f"ledger: {problem}", err=True)
    if problems:
        raise SystemExit(1)
    click.echo("ledger: ok")


@ledger_group.command("index")
@_ROOT_OPTION
def ledger_index_command(root: Path) -> None:
    """Regenerate ledger/README.md from the records."""
    faults, remedies, problems = load_ledger(root)
    if problems:
        for problem in problems:
            click.echo(f"ledger: {problem}", err=True)
        raise click.ClickException("fix the records before indexing")
    (root / index.README_NAME).write_text(index.render_index(faults, remedies), encoding="utf-8")
    click.echo(f"ledger: wrote {root / index.README_NAME}")
