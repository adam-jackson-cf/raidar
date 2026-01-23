"""Harness and model configuration for Harbor execution."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Agent(str, Enum):
    """Supported agents via Harbor."""

    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"
    GEMINI = "gemini"
    OPENHANDS = "openhands"


class ModelConfig(BaseModel):
    """Model configuration for LiteLLM."""

    provider: str = Field(description="Model provider (openai, anthropic, etc)")
    model_name: str = Field(description="Model identifier within provider")

    @property
    def litellm_model(self) -> str:
        """Return the LiteLLM-compatible model string."""
        return f"{self.provider}/{self.model_name}"

    @classmethod
    def from_string(cls, model_string: str) -> "ModelConfig":
        """Parse a model string like 'openai/gpt-5' into ModelConfig."""
        if "/" not in model_string:
            raise ValueError(f"Model string must be in format 'provider/model': {model_string}")
        provider, model_name = model_string.split("/", 1)
        return cls(provider=provider, model_name=model_name)


class HarnessConfig(BaseModel):
    """Configuration for harness/model combination."""

    agent: Agent = Field(description="Agent to use (claude-code, codex, etc)")
    model: ModelConfig = Field(description="Model configuration")
    rules_variant: Literal["strict", "minimal", "none"] = Field(
        default="strict",
        description="Rules variant to inject",
    )
    timeout_sec: int = Field(default=1800, description="Task timeout in seconds")

    def adapter(self):  # type: ignore[override]
        """Resolve the registered adapter for this harness."""
        from .adapters.registry import registry

        return registry.resolve(self)
