"""Codex CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from ..config import HarnessConfig
from ..fast_mode import fast_agent_import_path, with_harness_pythonpath
from .base import HarnessAdapter


class CodexCliAdapter(HarnessAdapter):
    """Adapter enforcing Codex CLI harness + model pairing."""

    HARBOR_AGENT_NAME = "codex"
    CLI_ENV_VAR = "CODEX_CLI_PATH"
    OPENAI_API_ENV = "OPENAI_API_KEY"
    MODEL_ALIAS_MAP: dict[str, tuple[str, str]] = {
        "gpt-5.2-low": ("gpt-5.2-codex", "low"),
        "gpt-5.2-medium": ("gpt-5.2-codex", "medium"),
        "gpt-5.2-high": ("gpt-5.2-codex", "high"),
    }

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
        if not os.environ.get(self.OPENAI_API_ENV):
            raise OSError("Codex Harbor runs require an API key. Set OPENAI_API_KEY.")

    def harbor_agent(self) -> str:
        return self.HARBOR_AGENT_NAME

    def harbor_agent_import_path(self) -> str | None:
        return fast_agent_import_path(self.config.agent)

    def _resolve_model_alias(self) -> tuple[str, str | None]:
        mapped = self.MODEL_ALIAS_MAP.get(self.config.model.name)
        if not mapped:
            return self.config.model.name, None
        return mapped[0], mapped[1]

    def model_argument(self) -> str:
        model_name, _ = self._resolve_model_alias()
        return f"{self.config.model.provider}/{model_name}"

    def extra_harbor_args(self) -> Iterable[str]:
        _, reasoning_effort = self._resolve_model_alias()
        if not reasoning_effort:
            return []
        return ["--ak", f"reasoning_effort={reasoning_effort}"]

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        cli_path = self._resolve_cli()
        env[self.CLI_ENV_VAR] = cli_path
        return with_harness_pythonpath(env)

    def prepare_workspace(self, workspace: Path) -> None:
        # Ensure Codex CLI has session directory path recorded for parsers
        codex_session_dir = workspace / ".codex"
        codex_session_dir.mkdir(exist_ok=True)
