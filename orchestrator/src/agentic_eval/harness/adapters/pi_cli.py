"""Pi CLI harness adapter."""

from __future__ import annotations

from ..config import HarnessConfig
from .external_cli import ExternalCliAdapter


class PiCliAdapter(ExternalCliAdapter):
    """Adapter for Pi harness submissions."""

    CLI_ENV_VAR = "PI_CLI_PATH"
    DEFAULT_BINARY = "pi"
    REQUIRED_ENV_VARS = ("PI_API_TOKEN",)

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
