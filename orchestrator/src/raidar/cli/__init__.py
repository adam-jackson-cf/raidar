"""CLI entrypoint for the Raidar experiment orchestrator."""

from __future__ import annotations

import os

import click
from dotenv import load_dotenv

from raidar.cli.register import register_commands
from raidar.cli.shared import ENV_PATH, SECRETS_SOURCE_ENV

# orchestrator/.env is the fallback secret source. When the make targets inject secrets
# from Infisical they set RAIDAR_SECRETS_SOURCE=infisical, and .env stays unread so a
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
