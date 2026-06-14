"""Gemini CLI harness adapter."""

from __future__ import annotations

import os

from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class GeminiCliAdapter(HarborCliAdapter):
    """Adapter enforcing Gemini harness + model pairing."""

    HARBOR_HARNESS_NAME = "gemini-cli"
    REQUIRED_PROVIDER = "google"
    ADAPTER_LABEL = "Gemini adapter"
    REASONING_UNSUPPORTED_MESSAGE = (
        "Gemini adapter does not yet expose normalized reasoning_effort controls. "
        "Use the default model behavior for now."
    )
    CLI_ENV_VAR = "GEMINI_CLI_PATH"
    DEFAULT_BINARY = "gemini"
    WORKSPACE_SESSION_DIR = ".gemini"
    GEMINI_API_ENV = "GEMINI_API_KEY"
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {
        "gemini-3.1-pro-preview": SupportedModelProfile(
            display_label="Gemini 3.1 Pro Preview",
        ),
        "gemini-3-pro-preview": SupportedModelProfile(
            display_label="Gemini 3 Pro Preview",
        ),
        "gemini-3-flash-preview": SupportedModelProfile(
            display_label="Gemini 3 Flash Preview",
        ),
    }

    def _validate_credentials(self) -> None:
        if not os.environ.get(self.GEMINI_API_ENV):
            raise OSError("Gemini Harbor runs require an API key. Set GEMINI_API_KEY.")
