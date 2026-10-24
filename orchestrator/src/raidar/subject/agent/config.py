"""Agent names, CLI install ids and AgentSpec configuration for Harbor execution.

Schema v2 names an agent by its Harbor ``AgentName`` (``codex``,
``claude-code``): an agent's identity is that name plus model, kwargs
(``reasoning_effort``) and the pinned version. ``Harness`` keeps the CLI
install ids (``codex-cli``) that released component manifests and the Python
pack image recipe were built with; those artifacts are frozen byte for byte,
so the ids stay internal and ``agent_cli`` / ``agent_name_for`` map between them.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class AgentName(StrEnum):
    """Harbor ``AgentName`` ids an experiment's ``agents[].name`` may use."""

    CODEX = "codex"
    CLAUDE_CODE = "claude-code"
    GEMINI_CLI = "gemini-cli"
    CURSOR_CLI = "cursor-cli"
    COPILOT_CLI = "copilot-cli"
    PI = "pi"


class Harness(StrEnum):
    """CLI install ids (internal; frozen in released component manifests and pack keys)."""

    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"
    GEMINI = "gemini"
    CURSOR = "cursor"
    COPILOT = "copilot"
    PI = "pi"


_AGENT_CLI: dict[AgentName, Harness] = {
    AgentName.CODEX: Harness.CODEX_CLI,
    AgentName.CLAUDE_CODE: Harness.CLAUDE_CODE,
    AgentName.GEMINI_CLI: Harness.GEMINI,
    AgentName.CURSOR_CLI: Harness.CURSOR,
    AgentName.COPILOT_CLI: Harness.COPILOT,
    AgentName.PI: Harness.PI,
}
_CLI_AGENT: dict[Harness, AgentName] = {cli: name for name, cli in _AGENT_CLI.items()}


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
