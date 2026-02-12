"""Claude Code CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from ..config import HarnessConfig
from ..fast_mode import fast_agent_import_path, with_harness_pythonpath
from .base import HarnessAdapter


class ClaudeCodeCliAdapter(HarnessAdapter):
    """Adapter enforcing Claude Code CLI harness + model pairing."""

    HARBOR_AGENT_NAME = "claude-code"
    CLI_ENV_VAR = "CLAUDE_CODE_CLI_PATH"
    API_KEY_ENV = "CLAUDE_CODE_API_KEY"
    ANTHROPIC_API_ENV = "ANTHROPIC_API_KEY"
    SUPPORTED_MODELS: set[str] = {
        "claude-opus-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    }

    def __init__(self, config: HarnessConfig) -> None:
        super().__init__(config)
        self._cli_path: str | None = None

    def _resolve_cli(self) -> str:
        if self._cli_path:
            return self._cli_path
        candidate = os.environ.get(self.CLI_ENV_VAR)
        if not candidate:
            candidate = shutil.which("claude")
        if not candidate:
            raise FileNotFoundError(
                "Claude Code CLI not found. Set CLAUDE_CODE_CLI_PATH or add 'claude' to PATH."
            )
        self._cli_path = candidate
        return candidate

    def validate(self) -> None:
        provider = self.config.model.provider
        if provider != "anthropic":
            raise ValueError(
                "Claude Code CLI adapter only supports models with provider 'anthropic'. "
                f"Received '{provider}'."
            )
        if self.config.model.name not in self.SUPPORTED_MODELS:
            supported = ", ".join(sorted(self.SUPPORTED_MODELS))
            raise ValueError(
                "Claude Code CLI adapter only supports models: "
                f"{supported}. Received '{self.config.model.name}'."
            )
        self._resolve_cli()
        if not (os.environ.get(self.ANTHROPIC_API_ENV) or os.environ.get(self.API_KEY_ENV)):
            raise OSError(
                "Claude Code Harbor runs require an API key. "
                "Set ANTHROPIC_API_KEY or CLAUDE_CODE_API_KEY."
            )

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
        # Ensure Claude Code has session directory path recorded for parsers
        claude_session_dir = workspace / ".claude"
        claude_session_dir.mkdir(exist_ok=True)
