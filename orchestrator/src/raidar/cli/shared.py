"""Shared helpers for CLI command modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = ORCHESTRATOR_ROOT / ".env"
SECRETS_SOURCE_ENV = "RAIDAR_SECRETS_SOURCE"
AGENT_CHOICES: list[str] = []


def set_agent_choices(choices: list[str]) -> None:
    AGENT_CHOICES[:] = choices


def run_or_raise(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(cmd)
    click.echo(f"[exec] {rendered}")
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise click.ClickException(f"Command failed ({result.returncode}): {rendered}")


def python_cmd() -> str:
    return sys.executable
