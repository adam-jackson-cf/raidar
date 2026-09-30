"""Register all CLI command groups."""

from __future__ import annotations

import click

from raidar.commands import env_harbor, harness, quality, workflow


def register_commands(main: click.Group) -> None:
    """Attach the experiment workflow, then the operational commands."""
    workflow.register(main)
    harness.register(main)
    env_harbor.register(main)
    quality.register(main)
