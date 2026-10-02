"""Agent discovery, validation and authentication CLI commands (``raidar agent``).

Agents are named by Harbor ``AgentName`` (``codex``, ``claude-code``, ...); each maps
to the CLI install id its adapter and the pack image use internally.
"""

from __future__ import annotations

import subprocess

import click

from raidar.agents.adapters.base import resolve_cli_executable
from raidar.agents.adapters.factory import adapter_class_for_harness, resolve_adapter
from raidar.agents.config import AgentName, AgentSpec, Harness, ModelTarget, agent_cli
from raidar.codex_auth import (
    CODEX_AUTH_MODE_ENV,
    codex_auth_json_path,
    has_file_backed_codex_auth,
)
from raidar.commands.shared import AGENT_CHOICES
from raidar.harness import harness_definition


def register(main: click.Group) -> None:
    main.add_command(agent)


@click.group()
def agent() -> None:
    """Agent discovery and validation workflows."""


@agent.command("list")
def agent_list() -> None:
    """List supported agents (Harbor names), their rule files and model coverage."""
    click.echo("Supported agents:")
    for name in AgentName:
        cli = agent_cli(name)
        adapter_class = adapter_class_for_harness(cli)
        rule_filename = harness_definition(cli.value).rule_filename or "(no rule mapping)"
        click.echo(f"  {name.value:12} -> {rule_filename}  (CLI {cli.value})")
        click.echo(f"  {'':12} models: {adapter_class.supported_model_summary()}")


@agent.command("validate")
@click.option("--agent", "-a", "agent_name", type=click.Choice(AGENT_CHOICES), required=True)
@click.option("--model", "-m", type=str, required=True)
@click.option("--provider", "-p", type=str, required=True)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "xhigh", "max"]),
)
@click.option("--timeout", type=int, default=1800)
def agent_validate(agent_name: str, **options) -> None:
    """Validate an agent's adapter wiring, model support and host credentials."""
    config = AgentSpec(
        harness=agent_cli(agent_name),
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

    click.echo("Agent validation passed.")
    click.echo(f"  agent: {agent_name}")
    click.echo(f"  provider: {options['provider']}")
    click.echo(f"  model: {options['model']}")
    if options["reasoning_effort"] is not None:
        click.echo(f"  reasoning_effort: {options['reasoning_effort']}")
    click.echo(f"  harbor_agent: {adapter.harbor_harness()}")
    click.echo(f"  model_argument: {adapter.model_argument()}")
    for key, value in adapter.execution_metadata().items():
        if value is not None:
            click.echo(f"  {key}: {value}")
    click.echo(f"  runtime_env_keys: {', '.join(runtime_keys) if runtime_keys else '(none)'}")


@agent.command("setup-auth")
@click.option("--agent", "-a", "agent_name", type=click.Choice(AGENT_CHOICES), required=True)
@click.option("--device-auth", is_flag=True)
def agent_setup_auth(agent_name: str, device_auth: bool) -> None:
    """Set up agent authentication for supported interactive providers."""
    if agent_name != AgentName.CODEX.value:
        raise click.ClickException(
            f"Agent auth setup is only implemented for {AgentName.CODEX.value}."
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
