"""Copilot CLI harness adapter."""

from __future__ import annotations

from ..config import HarnessConfig
from .external_cli import ExternalCliAdapter


class CopilotCliAdapter(ExternalCliAdapter):
    """Adapter for Copilot CLI harness."""

    CLI_ENV_VAR = "COPILOT_CLI_PATH"
    DEFAULT_BINARY = "copilot"
    REQUIRED_ENV_VARS = ("COPILOT_API_KEY",)

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
