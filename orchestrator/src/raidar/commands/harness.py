"""Harness discovery and authentication CLI commands."""

from __future__ import annotations

import subprocess

import click

from raidar.agents.adapters.base import resolve_cli_executable
from raidar.agents.adapters.codex_auth import (
    CODEX_AUTH_MODE_ENV,
    codex_auth_json_path,
    has_file_backed_codex_auth,
)
from raidar.agents.adapters.factory import adapter_class_for_harness, resolve_adapter
from raidar.agents.config import AgentSpec, Harness, ModelTarget
from raidar.agents.rules import SYSTEM_RULES
from raidar.commands.shared import HARNESS_CHOICES


def register(main: click.Group) -> None:
    main.add_command(harness)


@click.group()
def harness() -> None:
    """Harness discovery and validation workflows."""


@harness.command("list")
def harness_list() -> None:
    """List supported harness adapters, rule files, and model coverage."""
    click.echo("Supported harnesses:")
    for harness_name in Harness:
        adapter_class = adapter_class_for_harness(harness_name)
        click.echo(
            f"  {harness_name.value:12} -> {SYSTEM_RULES.get(harness_name, '(no rule mapping)')}"
        )
        click.echo(f"  {'':12} models: {adapter_class.supported_model_summary()}")


@harness.command("validate")
@click.option("--harness", "-a", type=click.Choice(HARNESS_CHOICES), required=True)
@click.option("--model", "-m", type=str, required=True)
@click.option("--provider", "-p", type=str, required=True)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
)
@click.option("--timeout", type=int, default=1800)
def harness_validate(**options) -> None:
    """Validate harness adapter wiring and environment requirements."""
    config = AgentSpec(
        harness=Harness(options["harness"]),
        model=ModelTarget(
            provider=options["provider"],
            name=options["model"],
            reasoning_effort=options["reasoning_effort"],
        ),
        timeout_sec=options["timeout"],
    )
    adapter = resolve_adapter(config)
    adapter.validate()
    runtime_keys = sorted(adapter.runtime_env().keys())

    click.echo("Harness validation passed.")
    click.echo(f"  harness: {options['harness']}")
    click.echo(f"  provider: {options['provider']}")
    click.echo(f"  model: {options['model']}")
    if options["reasoning_effort"] is not None:
        click.echo(f"  reasoning_effort: {options['reasoning_effort']}")
    click.echo(f"  harbor_harness: {adapter.harbor_harness()}")
    click.echo(f"  model_argument: {adapter.model_argument()}")
    for key, value in adapter.execution_metadata().items():
        if value is not None:
            click.echo(f"  {key}: {value}")
    click.echo(f"  runtime_env_keys: {', '.join(runtime_keys) if runtime_keys else '(none)'}")


@harness.command("setup-auth")
@click.option("--harness", "-a", type=click.Choice(HARNESS_CHOICES), required=True)
@click.option("--device-auth", is_flag=True)
def harness_setup_auth(harness: str, device_auth: bool) -> None:
    """Set up harness authentication for supported interactive providers."""
    if harness != Harness.CODEX_CLI.value:
        raise click.ClickException(
            f"Harness auth setup is only implemented for {Harness.CODEX_CLI.value}."
        )

    auth_path = codex_auth_json_path()
    if has_file_backed_codex_auth(auth_path):
        click.echo("Codex auth is already configured.")
        click.echo("  auth_mode: chatgpt")
        click.echo(f"  auth_json_path: {auth_path}")
        return

    command = [
        resolve_cli_executable(
            cli_env_var="CODEX_CLI_PATH",
            default_binary="codex",
            harness_label=Harness.CODEX_CLI.value,
        ),
        "login",
    ]
    if device_auth:
        command.append("--device-auth")

    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise click.ClickException(
            f"Codex login failed with exit code {completed.returncode}. "
            "Resolve the login error and retry `make codex-auth-setup`."
        )

    if not has_file_backed_codex_auth(auth_path):
        raise click.ClickException(
            "Codex login completed but no file-backed auth.json was found. "
            f"Configure Codex to use file-backed credentials in {auth_path.parent} "
            f'(for example via `cli_auth_credentials_store = "file"`) and retry. '
            f"{CODEX_AUTH_MODE_ENV}=chatgpt requires file-backed auth."
        )

    click.echo("Codex auth setup complete.")
    click.echo("  auth_mode: chatgpt")
    click.echo(f"  auth_json_path: {auth_path}")
