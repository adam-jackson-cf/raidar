"""Register the reboot CLI commands (spec, packs, assess, problems, jev, execution, analysis)."""

from __future__ import annotations

import click

from raidar.commands import analyze, assess, execution, experiment_spec, jev, pack, problem


def register(main: click.Group) -> None:
    """Attach the reboot commands to the public CLI group."""
    experiment_spec.register(main)
    pack.register(main)
    assess.register(main)
    problem.register(main)
    jev.register(main)
    execution.register(main)
    analyze.register(main)
