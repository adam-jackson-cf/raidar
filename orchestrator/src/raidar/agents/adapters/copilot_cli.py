"""Copilot CLI agent adapter."""

from __future__ import annotations

from ..config import AgentRunConfig
from .external_cli import ExternalCliAdapter


class CopilotCliAdapter(ExternalCliAdapter):
    """Adapter for Copilot CLI agent."""

    CLI_ENV_VAR = "COPILOT_CLI_PATH"
    DEFAULT_BINARY = "copilot"
    REQUIRED_ENV_VARS = ("COPILOT_API_KEY",)
    ALLOWED_PROVIDERS = ("github",)

    def __init__(self, config: AgentRunConfig) -> None:
        super().__init__(config)
