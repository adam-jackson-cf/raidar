"""Pi CLI agent adapter."""

from __future__ import annotations

from ..config import AgentRunConfig
from .external_cli import ExternalCliAdapter


class PiCliAdapter(ExternalCliAdapter):
    """Adapter for Pi agent submissions."""

    CLI_ENV_VAR = "PI_CLI_PATH"
    DEFAULT_BINARY = "pi"
    REQUIRED_ENV_VARS = ("PI_API_TOKEN",)
    ALLOWED_PROVIDERS = ("inflection",)

    def __init__(self, config: AgentRunConfig) -> None:
        super().__init__(config)
