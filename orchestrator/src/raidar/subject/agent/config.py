"""Agent names, CLI install ids and AgentSpec configuration for Harbor execution.

Schema v3 names an agent by its Harbor ``AgentName`` (``codex``,
``claude-code``): an agent's identity is that name plus model, reasoning and the
pinned version. ``Harness`` keeps the CLI install ids (``codex-cli``) that the
Python pack image recipe was built with; that artifact is frozen byte for byte,
so the ids stay internal and ``agent_cli`` / ``agent_name_for`` map between them.
"""

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, Field

from raidar.subject.agent.schema import AgentName


class Harness(StrEnum):
    """CLI install ids (internal; frozen in pack keys)."""

    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"


_AGENT_CLI: dict[AgentName, Harness] = {
    AgentName.CODEX: Harness.CODEX_CLI,
    AgentName.CLAUDE_CODE: Harness.CLAUDE_CODE,
}
_CLI_AGENT: dict[Harness, AgentName] = {cli: name for name, cli in _AGENT_CLI.items()}
HARBOR_AGENT_NAMES: Mapping[Harness, str] = {cli: name.value for cli, name in _CLI_AGENT.items()}
"""Harbor 0.23 installed agents Raidar drives, by CLI install id (``codex-cli`` -> ``codex``)."""


def agent_cli(name: AgentName | str) -> Harness:
    """The CLI install id behind a Harbor agent name."""
    return _AGENT_CLI[AgentName(name)]


def agent_name_for(cli: Harness | str) -> AgentName:
    """The Harbor agent name of a CLI install id (``codex-cli`` -> ``codex``)."""
    return _CLI_AGENT[Harness(cli)]


class ModelTarget(BaseModel):
    """Model descriptor paired with a harness."""

    provider: str = Field(description="Model provider (openai, anthropic, etc)")
    name: str = Field(description="Model identifier within provider")
    reasoning_effort: str | None = Field(
        default=None,
        description="Optional normalized reasoning/thinking effort for supported models",
    )

    @property
    def qualified_name(self) -> str:
        """Return provider/model string expected by Harbor and adapters."""
        return f"{self.provider}/{self.name}"


class AgentSpec(BaseModel):
    """Configuration for an AgentSpec: one agent CLI plus one model."""

    harness: Harness = Field(description="Agent CLI install id (claude-code, codex-cli, etc)")
    model: ModelTarget = Field(description="Model configuration")
    timeout_sec: int = Field(default=1800, description="Agent timeout in seconds (agent validate)")
