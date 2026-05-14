"""Gemini CLI harness adapter."""

from __future__ import annotations

import os
from collections.abc import Iterable

from ..config import AgentSpec
from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class GeminiCliAdapter(HarborCliAdapter):
    """Adapter enforcing Gemini harness + model pairing."""

    HARBOR_HARNESS_NAME = "gemini-cli"
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

    @classmethod
    def supported_model_summary(cls) -> str:
        return ", ".join(f"google/{model}" for model in sorted(cls.SUPPORTED_MODELS))

    def __init__(self, config: AgentSpec) -> None:
        super().__init__(config)

    def validate(self) -> None:
        provider = self.config.model.provider
        if provider != "google":
            raise ValueError(
                "Gemini adapter only supports models with provider 'google'. "
                f"Received '{provider}'."
            )
        if self.config.model.name not in self.SUPPORTED_MODELS:
            supported = ", ".join(sorted(self.SUPPORTED_MODELS))
            raise ValueError(
                "Gemini adapter only supports models: "
                f"{supported}. Received '{self.config.model.name}'."
            )
        if self.config.model.reasoning_effort is not None:
            raise ValueError(
                "Gemini adapter does not yet expose normalized reasoning_effort controls. "
                "Use the default model behavior for now."
            )
        self._resolve_cli()
        if not os.environ.get(self.GEMINI_API_ENV):
            raise OSError("Gemini Harbor runs require an API key. Set GEMINI_API_KEY.")

    def extra_harbor_args(self) -> Iterable[str]:
        return []
