"""Claude Code CLI harness adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from ..config import AgentSpec
from ..fast_mode import fast_harness_import_path, with_harness_pythonpath
from .base import HarnessAdapter


class ClaudeCodeCliAdapter(HarnessAdapter):
    """Adapter enforcing Claude Code CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "claude-code"
    CLI_ENV_VAR = "CLAUDE_CODE_CLI_PATH"
    API_KEY_ENV = "CLAUDE_CODE_API_KEY"
    ANTHROPIC_API_ENV = "ANTHROPIC_API_KEY"
    SUPPORTED_MODELS: set[str] = {
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    }
    SUPPORTED_REASONING: dict[str, tuple[str, ...]] = {
        "claude-opus-4-7": ("low", "medium", "high", "xhigh", "max"),
        "claude-opus-4-6": ("low", "medium", "high", "max"),
        "claude-sonnet-4-6": ("low", "medium", "high", "max"),
    }

    @classmethod
    def supported_model_summary(cls) -> str:
        return ", ".join(f"anthropic/{model}" for model in sorted(cls.SUPPORTED_MODELS))

    def __init__(self, config: AgentSpec) -> None:
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
        reasoning_effort = self.config.model.reasoning_effort
        if reasoning_effort is not None:
            allowed = self.SUPPORTED_REASONING.get(self.config.model.name, ())
            if reasoning_effort not in allowed:
                allowed_rendered = ", ".join(allowed) if allowed else "(none)"
                raise ValueError(
                    f"Model '{self.config.model.name}' only supports reasoning levels: "
                    f"{allowed_rendered}. Received '{reasoning_effort}'."
                )
        self._resolve_cli()
        if not (os.environ.get(self.ANTHROPIC_API_ENV) or os.environ.get(self.API_KEY_ENV)):
            raise OSError(
                "Claude Code Harbor runs require an API key. "
                "Set ANTHROPIC_API_KEY or CLAUDE_CODE_API_KEY."
            )

    def harbor_harness(self) -> str:
        return self.HARBOR_HARNESS_NAME

    def harbor_harness_import_path(self) -> str | None:
        return fast_harness_import_path(self.config.harness)

    def model_argument(self) -> str:
        return f"{self.config.model.provider}/{self.config.model.name}"

    def extra_harbor_args(self) -> Iterable[str]:
        default_effort = "high" if self.config.model.name in self.SUPPORTED_REASONING else None
        reasoning_effort = self.config.model.reasoning_effort or default_effort
        if not reasoning_effort:
            return []
        return ["--ak", "thinking_mode=adaptive", "--ak", f"effort={reasoning_effort}"]

    def runtime_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        cli_path = self._resolve_cli()
        env[self.CLI_ENV_VAR] = cli_path
        return with_harness_pythonpath(env)

    def prepare_workspace(self, workspace: Path) -> None:
        # Ensure Claude Code trace artifacts always have a stable home.
        claude_session_dir = workspace / ".claude"
        claude_session_dir.mkdir(exist_ok=True)
