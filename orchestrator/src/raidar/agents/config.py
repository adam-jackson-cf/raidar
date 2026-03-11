"""Harness and AgentSpec configuration for Harbor execution."""

from enum import StrEnum

from pydantic import BaseModel, Field


class Harness(StrEnum):
    """Supported harnesses via Harbor."""

    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"
    GEMINI = "gemini"
    CURSOR = "cursor"
    COPILOT = "copilot"
    PI = "pi"


class ModelTarget(BaseModel):
    """Model descriptor paired with a harness."""

    provider: str = Field(description="Model provider (openai, anthropic, etc)")
    name: str = Field(description="Model identifier within provider")

    @property
    def qualified_name(self) -> str:
        """Return provider/model string expected by Harbor and adapters."""
        return f"{self.provider}/{self.name}"

    @classmethod
    def from_string(cls, model_string: str) -> "ModelTarget":
        """Parse a model string like 'openai/gpt-5' into ModelTarget."""
        if "/" not in model_string:
            raise ValueError(f"Model string must be in format 'provider/model': {model_string}")
        provider, model_name = model_string.split("/", 1)
        return cls(provider=provider, name=model_name)


class AgentSpec(BaseModel):
    """Configuration for an AgentSpec: one harness plus one model."""

    harness: Harness = Field(description="Harness to use (claude-code, codex-cli, etc)")
    model: ModelTarget = Field(description="Model configuration")
    timeout_sec: int = Field(default=1800, description="Scenario timeout in seconds")

    def adapter(self):  # type: ignore[override]
        """Resolve the registered adapter for this harness."""
        from .adapters.registry import registry

        return registry.resolve(self)
