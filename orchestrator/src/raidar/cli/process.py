"""Run external commands for CLI commands, failing the command when they fail."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click


def run_or_raise(cmd: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> None:
    rendered = " ".join(cmd)
    click.echo(f"[exec] {rendered}")
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise click.ClickException(f"Command failed ({result.returncode}): {rendered}")
