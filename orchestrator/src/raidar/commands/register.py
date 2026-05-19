"""Register all CLI command groups."""

from __future__ import annotations

import click

from raidar.commands import (
    env_harbor,
    experiments,
    harness,
    matrix_report,
    quality,
    run_experiment,
    scenario,
)


def register_commands(main: click.Group) -> None:
    """Attach all concrete commands to the public CLI group."""
    run_experiment.register(main)
    quality.register(main)
    env_harbor.register(main)
    experiments.register(main)
    harness.register(main)
    scenario.register(main)
    matrix_report.register(main)
