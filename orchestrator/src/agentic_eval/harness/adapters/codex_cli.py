"""Codex CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..config import HarnessConfig
from .base import HarnessAdapter


class CodexCliAdapter(HarnessAdapter):
    """Adapter enforcing Codex CLI harness + model pairing."""

    CLI_ENV_VAR = "CODEX_CLI_PATH"
    API_KEY_ENV = "CODEX_API_KEY"
    OAUTH_ENV = "CODEX_OAUTH_TOKEN"

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
        self._cli_path: str | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _resolve_cli(self) -> str:
        if self._cli_path:
            return self._cli_path
        candidate = os.environ.get(self.CLI_ENV_VAR)
        if not candidate:
            candidate = shutil.which("codex")
        if not candidate:
            raise FileNotFoundError(
                "Codex CLI not found. Set CODEX_CLI_PATH or add 'codex' to PATH."
            )
        self._cli_path = candidate
        return candidate

    def validate(self) -> None:
        provider = self.config.model.provider
        if provider != "codex":
            raise ValueError(
                "Codex CLI adapter only supports models with provider 'codex'. "
                f"Received '{provider}'."
            )
        self._resolve_cli()
        if not (os.environ.get(self.API_KEY_ENV) or os.environ.get(self.OAUTH_ENV)):
            raise EnvironmentError(
                "Codex CLI requires credentials. Set CODEX_API_KEY or CODEX_OAUTH_TOKEN."
            )

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        cli_path = self._resolve_cli()
        env[self.CLI_ENV_VAR] = cli_path
        return env

    def prepare_workspace(self, workspace: Path) -> None:
        # Ensure Codex CLI has session directory path recorded for parsers
        codex_session_dir = workspace / ".codex"
        codex_session_dir.mkdir(exist_ok=True)
*** End of File
