"""`raidar assess`: offline assessment over stored runs.

``--regrade`` reruns the oracle over a trial's (or job's) stored per-step
snapshots and reports whether the results match the original run (plan E3).
Further modes (``--rescore``, plan F2) attach to this command.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from raidar.oracle.bundle import BundleError
from raidar.oracle.regrade import RegradeError, RegradeOptions, regrade_path


def register(main: click.Group) -> None:
    main.add_command(assess)


@click.command()
@click.option(
    "--regrade",
    "regrade_target",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Trial or job directory whose stored snapshots the oracle regrades.",
)
@click.option(
    "--bundle",
    "bundle_override",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Oracle bundle location if it moved (its digest must still match the record).",
)
@click.option("--docker-context", default="orbstack", show_default=True)
@click.option(
    "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write the report here."
)
def assess(
    regrade_target: Path | None,
    bundle_override: Path | None,
    docker_context: str,
    out: Path | None,
) -> None:
    """Re-assess stored runs without re-running agents."""
    if regrade_target is None:
        raise click.UsageError("choose an assessment mode: --regrade PATH")
    options = RegradeOptions(docker_context=docker_context, bundle_override=bundle_override)
    try:
        report = asyncio.run(regrade_path(regrade_target, options))
    except (RegradeError, BundleError) as exc:
        raise click.ClickException(str(exc)) from exc
    text = json.dumps(report, indent=2)
    if out is not None:
        out.write_text(text + "\n")
    click.echo(text)
    if not report["match"]:
        raise click.exceptions.Exit(1)
