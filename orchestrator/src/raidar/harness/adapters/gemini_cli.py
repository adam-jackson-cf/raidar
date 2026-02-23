"""Gemini CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from ..config import HarnessConfig
from ..fast_mode import fast_agent_import_path, with_harness_pythonpath
from .base import HarnessAdapter


class GeminiCliAdapter(HarnessAdapter):
    """Adapter enforcing Gemini harness + model pairing."""

    HARBOR_AGENT_NAME = "gemini-cli"
    CLI_ENV_VAR = "GEMINI_CLI_PATH"
    GEMINI_API_ENV = "GEMINI_API_KEY"
    SUPPORTED_MODELS: set[str] = {
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    }

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
        self._cli_path: str | None = None

    def _resolve_cli(self) -> str:
        if self._cli_path:
            return self._cli_path
        candidate = os.environ.get(self.CLI_ENV_VAR)
        if not candidate:
            candidate = shutil.which("gemini")
        if not candidate:
            raise FileNotFoundError(
                "Gemini CLI not found. Set GEMINI_CLI_PATH or add 'gemini' to PATH."
            )
        self._cli_path = candidate
        return candidate

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
        self._resolve_cli()
        if not os.environ.get(self.GEMINI_API_ENV):
            raise OSError("Gemini Harbor runs require an API key. Set GEMINI_API_KEY.")

    def harbor_agent(self) -> str:
        return self.HARBOR_AGENT_NAME

    def harbor_agent_import_path(self) -> str | None:
        return fast_agent_import_path(self.config.agent)

    def model_argument(self) -> str:
        return f"{self.config.model.provider}/{self.config.model.name}"

    def extra_harbor_args(self) -> Iterable[str]:
        return []

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        cli_path = self._resolve_cli()
        env[self.CLI_ENV_VAR] = cli_path
        return with_harness_pythonpath(env)

    def prepare_workspace(self, workspace: Path) -> None:
        # Ensure Gemini CLI has session directory path recorded for parsers
        gemini_session_dir = workspace / ".gemini"
        gemini_session_dir.mkdir(exist_ok=True)
