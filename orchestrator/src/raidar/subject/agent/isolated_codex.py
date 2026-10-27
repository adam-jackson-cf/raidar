"""Codex under Harbor, kept apart from its home and its container (design section 4).

Harbor's installed ``Codex`` agent runs ``codex exec`` (``codex exec resume --last`` on a
resumed turn) with ``CODEX_HOME=/tmp/codex-home``, a home it creates for the turn, links the
sign-in into and removes afterwards. ``IsolatedCodex`` applies the agent definition's
``settings`` on top, on every command it runs, first turn and resumed (factory-x-loops
``harness/codex.py``, adapted to Harbor and Raidar's container):

- a container with ``/etc/codex/config.toml`` or ``/etc/codex/requirements.toml`` is refused:
  codex applies them whatever ``CODEX_HOME`` is;
- a ``CODEX_HOME`` left at Harbor's path is removed before the turn, so the home holds only
  ``auth.json`` (and, on a resumed turn, the session store Harbor copies back to resume);
  a native config, ``OPENAI_BASE_URL`` or MCP servers, which Harbor would write into the
  home as ``config.toml``, and a Harbor ``skills_dir``, which Harbor copies into the
  container user's ``~/.agents/skills``, are refused;
- the command carries ``--ignore-user-config``, ``--disable <feature>`` for each of
  ``features_off`` and one ``-c skills.config=[...]`` switching off, by ``SKILL.md`` path,
  each bundled skill under ``$CODEX_HOME/skills/.system`` and every ``SKILL.md`` found in
  the agent user's ``~/.agents/skills`` and in ``/etc/codex/skills``. A directory path does
  not work and a name would also hide a workspace skill of that name, so paths are used;
  the workspace's own ``.agents/skills`` stay visible.

Before the command it reads, as the agent user with the command's environment and shell
start-up, what the agent process runs with: user, ``PATH`` and ``TMPDIR``. After a turn,
``harness_isolation`` holds what was applied (the workflow runner records it per turn) and
``agent_process`` what the process ran with (part of the trial's parity record).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, override

from harbor.agents.installed.codex import Codex, CodexOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

from raidar.subject.agent.codex_audit import SESSIONS_DIR, codex_last_message, codex_session_audit
from raidar.subject.agent.probe import parse_probe, probe_command
from raidar.subject.agent.protocol import Tokens, cumulative_accounting

MANAGED_CONFIGS = ("/etc/codex/config.toml", "/etc/codex/requirements.toml")
SYSTEM_SKILLS = "/etc/codex/skills"
USER_SKILLS = "$HOME/.agents/skills"
NVM = "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi"
"""Harbor's codex command sources nvm first; the process facts are read after the same."""


class CodexIsolationError(RuntimeError):
    """The container or the agent's configuration would let host settings reach codex."""


class IsolatedCodexOptions(CodexOptions):
    features_off: list[str] = Field(default_factory=list)
    bundled_skills: list[str] = Field(default_factory=list)


def skills_config(manifests: list[str]) -> str:
    """The ``skills.config`` value (a TOML inline array) switching ``manifests`` off.

    Paths are quoted as JSON strings, which TOML reads the same.
    """
    entries = ",".join(f"{{path={json.dumps(path)},enabled=false}}" for path in manifests)
    return f"[{entries}]"


def _probe_command() -> str:
    return probe_command(NVM, (f'"{USER_SKILLS}"', SYSTEM_SKILLS))


class IsolatedCodex(Codex):
    """Harbor's codex agent with the agent definition's isolation (module docstring)."""

    options_model = IsolatedCodexOptions

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._isolation_args: list[str] | None = None
        self.harness_isolation: dict[str, Any] | None = None
        self.agent_process: dict[str, str | None] | None = None

    @property
    def isolation_options(self) -> IsolatedCodexOptions:
        options = self.options
        if not isinstance(options, IsolatedCodexOptions):
            raise CodexIsolationError("IsolatedCodex was built without its options")
        return options

    @property
    def codex_home(self) -> str:
        return self._REMOTE_CODEX_HOME.as_posix()

    def bundled_manifests(self) -> list[str]:
        home = self.codex_home
        return [
            f"{home}/skills/.system/{name}/SKILL.md"
            for name in self.isolation_options.bundled_skills
        ]

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        await self.isolate(environment)
        await super().run(instruction, environment, context)

    async def isolate(self, environment: BaseEnvironment) -> None:
        """Refuse what the home cannot keep out, start a fresh home and fix the flags."""
        if self.skills_dir:
            raise CodexIsolationError(
                f"a Harbor skills_dir ({self.skills_dir}) would be copied into the container "
                "user's ~/.agents/skills; workflow skills go in the workspace"
            )
        checks = " ".join(f'[ -e {path} ] && echo "{path}";' for path in MANAGED_CONFIGS)
        found = await self.exec_as_root(
            environment, command=f"{checks} rm -rf {shlex.quote(self.codex_home)}"
        )
        managed = [line for line in (found.stdout or "").splitlines() if line.strip()]
        if managed:
            raise CodexIsolationError(
                f"codex applies {managed} whatever CODEX_HOME is: remove them from the image"
            )
        probe = await self.exec_as_agent(
            environment, command=_probe_command(), env={"CODEX_HOME": self.codex_home}
        )
        facts, host_skills = parse_probe(probe.stdout or "")
        options = self.isolation_options
        disabled = [*self.bundled_manifests(), *host_skills]
        args = ["--ignore-user-config"]
        for feature in options.features_off:
            args += ["--disable", feature]
        if disabled:
            args += ["-c", f"skills.config={skills_config(disabled)}"]
        self._isolation_args = args
        self.agent_process = facts
        self.harness_isolation = {
            "codex_home": f"{self.codex_home}: created for the turn, holding only auth.json",
            "refused_if_present": list(MANAGED_CONFIGS),
            "features_off": list(options.features_off),
            "skills_off": disabled,
            "flags": args,
        }

    @override
    def build_cli_flags(self) -> str:
        if self._isolation_args is None:
            raise CodexIsolationError("codex flags were asked for before the turn was isolated")
        base = super().build_cli_flags()
        return " ".join([*([base] if base else []), *map(shlex.quote, self._isolation_args)])

    @override
    def _build_effective_config(self, openai_base_url: str | None = None) -> dict[str, Any]:
        config = super()._build_effective_config(openai_base_url)
        if config:
            raise CodexIsolationError(
                f"Harbor would write config.toml ({sorted(config)}) into CODEX_HOME, which "
                "must hold only auth.json"
            )
        return config

    def process_facts(self) -> dict[str, str | None] | None:
        return self.agent_process

    def accounting(
        self, context: AgentContext, previous: Tokens | None
    ) -> tuple[Tokens | None, Tokens | None]:
        """Codex reports the session's running total: the delta is that less the previous one."""
        return cumulative_accounting(context, previous)

    def last_message(self, turn_dir: Path) -> str | None:
        return codex_last_message(turn_dir)

    def session_store(self) -> tuple[str, ...]:
        """Harbor copies ``sessions`` back into ``CODEX_HOME`` to resume the last session."""
        return (SESSIONS_DIR,)

    def session_audit(self, turn_dir: Path) -> dict[str, Any]:
        return codex_session_audit(turn_dir)
