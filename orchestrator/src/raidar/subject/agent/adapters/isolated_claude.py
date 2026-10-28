"""Claude Code under Harbor, kept apart from its home and its container (design section 4).

Harbor's installed ``ClaudeCode`` agent runs ``claude --print`` (``--continue`` on a resumed turn)
with ``CLAUDE_CONFIG_DIR=/logs/agent/sessions``, which is also where claude keeps the session
transcripts the next turn resumes from, so the home is kept between the turns of a session.
``IsolatedClaudeCode`` applies the agent definition's isolation on top, on every command it runs,
first turn and resumed (factory-x-loops ``harness/claude_code.py``, adapted to Harbor):

- a container with a managed settings, managed MCP or managed ``CLAUDE.md`` file under
  ``/etc/claude-code`` is refused: claude applies them whatever ``CLAUDE_CONFIG_DIR`` is;
- Harbor copies the container user's ``~/.claude/skills`` into the home before every command, so
  a container that has any is refused; a Harbor ``skills_dir``, ``memory_dir``, MCP servers or
  native settings file, which Harbor would put into the home, are refused too;
- a session's first turn starts from a home created for it (an earlier one is removed); a resumed
  turn keeps the transcripts and removes any skills, plugins, commands, agents, hooks, settings
  and ``CLAUDE.md`` found in the home. The sign-in is the credential environment Raidar hands the
  turn (``ANTHROPIC_API_KEY`` / ``CLAUDE_CODE_OAUTH_TOKEN``), not a file in the home;
- the command carries ``--setting-sources user,project,local`` (the user level is the empty home)
  and ``--strict-mcp-config``, and runs with ``DISABLE_AUTOUPDATER=1`` so the baked-in release is
  what is measured. Harbor's own ``--permission-mode=bypassPermissions`` stays: the trial
  container is the sandbox.

Before the command it reads, as the agent user with the command's shell start-up, what the agent
process runs with: user, ``PATH`` and ``TMPDIR``. After a turn ``harness_isolation`` holds what
was applied and ``agent_process`` what the process ran with.

Claude counts usage per call: each turn's usage is read from its own stream's ``result`` envelope
and the session total is the sum of the turns (``accounting``).
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from raidar.subject.agent.adapters.claude_audit import (
    SESSIONS_DIR,
    claude_last_message,
    claude_session_audit,
    claude_turn_usage,
)
from raidar.subject.agent.adapters.instructions import (
    instructions_target,
    place_instructions_command,
)
from raidar.subject.agent.adapters.probe import parse_probe, probe_command
from raidar.subject.agent.protocol import (
    Credentials,
    IsolationError,
    StatePath,
    Tokens,
    per_turn_accounting,
)

MANAGED_FILES = (
    "/etc/claude-code/managed-settings.json",
    "/etc/claude-code/managed-mcp.json",
    "/etc/claude-code/CLAUDE.md",
)
ISOLATION_FLAGS = ("--setting-sources", "user,project,local", "--strict-mcp-config")
FIXED_ENVIRONMENT = {"DISABLE_AUTOUPDATER": "1"}
STARTUP = 'export PATH="$HOME/.local/bin:$PATH"'
"""Harbor's claude command puts ``~/.local/bin`` first; the process facts are read after it."""
USER_SKILLS = '"$HOME/.claude/skills"'
HOME_ARTIFACTS = (
    "skills",
    "plugins",
    "commands",
    "agents",
    "hooks",
    "settings.json",
    "settings.local.json",
    "CLAUDE.md",
)
"""What claude would read from the home as skills, instructions or settings."""
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
CREDENTIAL_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", OAUTH_TOKEN_ENV)
"""Harbor's claude-code agent signs in with an API key, an auth token or a subscription token."""
FORCE_OAUTH_ENV = "CLAUDE_FORCE_OAUTH"
SECRETS_HINT = "(Infisical project mac-os-cf, env dev, via make; else orchestrator/.env)"


class ClaudeIsolationError(IsolationError):
    """The container or the agent's configuration would let host settings reach claude."""


class IsolatedClaudeCode(ClaudeCode):
    """Harbor's claude-code agent with the agent definition's isolation (module docstring)."""

    def __init__(
        self,
        *args: Any,
        instructions: str | None = None,
        instructions_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._instructions = instructions
        self._instructions_path = instructions_path
        self.harness_isolation: dict[str, Any] | None = None
        self.agent_process: dict[str, str | None] | None = None

    @property
    def claude_home(self) -> str:
        return (self.environment_logs_dir / SESSIONS_DIR).as_posix()

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        await self.isolate(environment)
        await super().run(instruction, environment, context)

    def _refuse_native_inputs(self) -> None:
        if self.skills_dir:
            raise ClaudeIsolationError(
                f"a Harbor skills_dir ({self.skills_dir}) would be copied into CLAUDE_CONFIG_DIR; "
                "workflow skills go in the workspace"
            )
        if self.options.memory_dir:
            raise ClaudeIsolationError("a Harbor memory_dir would be copied into CLAUDE_CONFIG_DIR")
        if self.mcp_servers:
            raise ClaudeIsolationError("Harbor MCP servers would be written into CLAUDE_CONFIG_DIR")
        if self.config_source is not None:
            raise ClaudeIsolationError("a Harbor native config would be applied as --settings")

    def _prepare_command(self) -> str:
        """The root command: report managed files, then clean the home for this turn."""
        home = shlex.quote(self.claude_home)
        if self._resume:
            clean = "rm -rf " + " ".join(f"{home}/{name}" for name in HOME_ARTIFACTS)
        else:
            clean = f"rm -rf {home}"
        checks = " ".join(f'[ -e "{path}" ] && echo "{path}";' for path in MANAGED_FILES)
        return f"{checks} {clean}"

    def _isolation_record(self) -> dict[str, Any]:
        state = "kept for the session, host-readable files removed" if self._resume else "created"
        return {
            "claude_config_dir": (
                f"{self.claude_home}: {state}; sign-in from the turn's credential environment"
            ),
            "refused_if_present": [*MANAGED_FILES, "$HOME/.claude/skills"],
            "environment": dict(FIXED_ENVIRONMENT),
            "flags": list(ISOLATION_FLAGS),
        }

    async def isolate(self, environment: BaseEnvironment) -> None:
        """Refuse what the home cannot keep out, clean the home and fix the flags."""
        self._refuse_native_inputs()
        found = await self.exec_as_root(environment, command=self._prepare_command())
        managed = [line for line in (found.stdout or "").splitlines() if line.strip()]
        if managed:
            raise ClaudeIsolationError(
                f"claude applies {managed} whatever CLAUDE_CONFIG_DIR is: remove them from the "
                "image"
            )
        probe = await self.exec_as_agent(
            environment,
            command=probe_command(STARTUP, (USER_SKILLS,)),
            env={"CLAUDE_CONFIG_DIR": self.claude_home},
        )
        facts, host_skills = parse_probe(probe.stdout or "")
        if host_skills:
            raise ClaudeIsolationError(
                f"Harbor would copy the container user's ~/.claude/skills ({host_skills}) into "
                "CLAUDE_CONFIG_DIR: remove them from the image"
            )
        if self._instructions is not None:
            await self.exec_as_agent(
                environment,
                command=place_instructions_command(
                    instructions_target(self._instructions_path, self.claude_home),
                    self._instructions,
                ),
                env={"CLAUDE_CONFIG_DIR": self.claude_home},
            )
        self.agent_process = facts
        self.harness_isolation = self._isolation_record()

    @override
    def build_cli_flags(self) -> str:
        base = super().build_cli_flags()
        return " ".join([*([base] if base else []), *map(shlex.quote, ISOLATION_FLAGS)])

    @override
    def _resolve_auth_env(self) -> dict[str, str]:
        return {**super()._resolve_auth_env(), **FIXED_ENVIRONMENT}

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Harbor's trajectory over the whole transcript store, but the turn's own usage.

        Harbor sums the store, which holds every turn of the session; the turn's tokens are the
        ``result`` envelopes of its own stream. Without one the turn's usage is unknown.
        """
        super().populate_context_post_run(context)
        usage = claude_turn_usage(self.logs_dir)
        context.n_input_tokens = usage.input if usage else None
        context.n_cache_tokens = usage.cached if usage else None
        context.n_output_tokens = usage.output if usage else None
        context.cost_usd = usage.cost_usd if usage else None

    def process_facts(self) -> dict[str, str | None] | None:
        return self.agent_process

    def accounting(
        self, context: AgentContext, previous: Tokens | None
    ) -> tuple[Tokens | None, Tokens | None]:
        """Claude reports per call: the delta is the turn's usage, the total adds it up."""
        return per_turn_accounting(context, previous)

    def last_message(self, turn_dir: Path) -> str | None:
        return claude_last_message(turn_dir)

    def session_store(self) -> tuple[str, ...]:
        """``CLAUDE_CONFIG_DIR`` holds the transcripts ``claude --continue`` resumes from."""
        return (SESSIONS_DIR,)

    def session_audit(self, turn_dir: Path) -> dict[str, Any]:
        return claude_session_audit(turn_dir)

    @classmethod
    def credentials(cls, env: Mapping[str, str]) -> Credentials:
        """An API key, an auth token or an OAuth token; ``CLAUDE_FORCE_OAUTH`` needs the latter."""

        def present(name: str) -> bool:
            return bool(env.get(name, "").strip())

        forced = env.get(FORCE_OAUTH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        if forced and not present(OAUTH_TOKEN_ENV):
            return Credentials(f"{FORCE_OAUTH_ENV} is set but {OAUTH_TOKEN_ENV} is not")
        if any(present(name) for name in CREDENTIAL_NAMES):
            return Credentials(None)
        names = ", ".join(CREDENTIAL_NAMES)
        return Credentials(f"no credential: set one of {names} {SECRETS_HINT}")

    @classmethod
    def state_paths(cls) -> tuple[StatePath, ...]:
        return (
            StatePath("${RAIDAR_CLAUDE_CONFIG_DIR:-$LOGS_DIR/sessions}", "claude-config", "dir"),
            StatePath("$HOME_DIR/.claude", "claude-config", "dir"),
            StatePath("$HOME_DIR/.claude.json", "claude.json", "file"),
        )
