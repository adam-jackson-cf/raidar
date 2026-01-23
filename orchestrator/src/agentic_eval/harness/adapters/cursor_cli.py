"""Cursor CLI harness adapter."""

from __future__ import annotations

from ..config import HarnessConfig
from .external_cli import ExternalCliAdapter


class CursorCliAdapter(ExternalCliAdapter):
    """Adapter for Cursor CLI harness."""

    CLI_ENV_VAR = "CURSOR_CLI_PATH"
    DEFAULT_BINARY = "cursor"
    REQUIRED_ENV_VARS = ("CURSOR_API_KEY",)

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
