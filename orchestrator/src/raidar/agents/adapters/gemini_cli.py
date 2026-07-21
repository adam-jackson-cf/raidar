"""Gemini CLI harness adapter."""

from __future__ import annotations

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
