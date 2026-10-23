"""Register all CLI command groups."""

from __future__ import annotations

import click

from raidar.commands import agent, env_harbor, health, ledger, quality, target, workflow


def register_commands(main: click.Group) -> None:
    """Attach the experiment workflow, then the operational commands."""
    workflow.register(main)
    agent.register(main)
    env_harbor.register(main)
    quality.register(main)
    health.register(main)
    ledger.register(main)
    target.register(main)
