"""Pi CLI harness adapter."""

from __future__ import annotations

from ..config import AgentSpec
from .external_cli import ExternalCliAdapter


class PiCliAdapter(ExternalCliAdapter):
    """Adapter for the Pi CLI harness."""

    CLI_ENV_VAR = "PI_CLI_PATH"
    DEFAULT_BINARY = "pi"
    REQUIRED_ENV_VARS = ("PI_API_TOKEN",)
    ALLOWED_PROVIDERS = ("inflection",)

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
