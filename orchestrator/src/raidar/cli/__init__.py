"""CLI entrypoint for the Raidar experiment orchestrator."""

from __future__ import annotations

import os

import click
from dotenv import load_dotenv

from raidar.cli.register import register_commands
from raidar.contract.paths import ORCHESTRATOR_ROOT

ENV_PATH = ORCHESTRATOR_ROOT / ".env"
SECRETS_SOURCE_ENV = "RAIDAR_SECRETS_SOURCE"

# orchestrator/.env supplies secrets unless the make targets inject them from
# Infisical (they set RAIDAR_SECRETS_SOURCE=infisical), in which case .env stays unread so a
# stale local value can never stand in for a secret Infisical lacks.
if ENV_PATH.exists() and os.environ.get(SECRETS_SOURCE_ENV) != "infisical":
    load_dotenv(ENV_PATH, override=False)


@click.group()
@click.version_option(package_name="raidar")
def main() -> None:
    """Raidar: lock, run, assess and analyze coding-agent experiments."""


register_commands(main)


if __name__ == "__main__":
    main()
