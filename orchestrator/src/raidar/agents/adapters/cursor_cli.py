"""Cursor CLI harness adapter."""

from __future__ import annotations

from ..config import AgentSpec
from .external_cli import ExternalCliAdapter


class CursorCliAdapter(ExternalCliAdapter):
    """Adapter for the Cursor CLI harness."""

    CLI_ENV_VAR = "CURSOR_CLI_PATH"
    DEFAULT_BINARY = "cursor"
    REQUIRED_ENV_VARS = ("CURSOR_API_KEY",)
    ALLOWED_PROVIDERS = ("cursor", "openai", "anthropic", "google", "deepseek")

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)
