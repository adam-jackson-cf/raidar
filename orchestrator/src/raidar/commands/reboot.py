"""Register the reboot CLI commands (experiment spec, runtime packs, assess)."""

from __future__ import annotations

import click

from raidar.commands import assess, experiment_spec, pack


def register(main: click.Group) -> None:
    """Attach the reboot commands to the public CLI group."""
    experiment_spec.register(main)
    pack.register(main)
    assess.register(main)
