"""CLI entrypoint for the scenario/experiment orchestrator."""

from __future__ import annotations

import click
from dotenv import load_dotenv

from raidar.agents.config import Harness
from raidar.commands.register import register_commands
from raidar.commands.shared import ENV_PATH, set_harness_choices

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(package_name="raidar")
def main() -> None:
    """Scenario/experiment orchestrator for harness/model evaluation runs."""


set_harness_choices([harness.value for harness in Harness])
register_commands(main)


if __name__ == "__main__":
    main()
