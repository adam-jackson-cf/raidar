"""Claude Code CLI harness adapter."""

from __future__ import annotations

import os
from collections.abc import Iterable

from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class ClaudeCodeCliAdapter(HarborCliAdapter):
    """Adapter enforcing Claude Code CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "claude-code"
    REQUIRED_PROVIDER = "anthropic"
    ADAPTER_LABEL = "Claude Code CLI adapter"
    CLI_ENV_VAR = "CLAUDE_CODE_CLI_PATH"
    DEFAULT_BINARY = "claude"
    WORKSPACE_SESSION_DIR = ".claude"
    API_KEY_ENV = "CLAUDE_CODE_API_KEY"
    ANTHROPIC_API_ENV = "ANTHROPIC_API_KEY"
    OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {
        "claude-opus-4-7": SupportedModelProfile(
            display_label="Claude Opus 4.7",
            reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            default_reasoning="high",
        ),
        "claude-opus-4-6": SupportedModelProfile(
            display_label="Claude Opus 4.6",
            reasoning_levels=("low", "medium", "high", "max"),
            default_reasoning="high",
        ),
        "claude-sonnet-4-6": SupportedModelProfile(
            display_label="Claude Sonnet 4.6",
            reasoning_levels=("low", "medium", "high", "max"),
            default_reasoning="high",
        ),
        "claude-sonnet-4-5": SupportedModelProfile(
            display_label="Claude Sonnet 4.5",
        ),
        "claude-haiku-4-5": SupportedModelProfile(
            display_label="Claude Haiku 4.5",
        ),
    }

    def _validate_credentials(self) -> None:
        if not (
            os.environ.get(self.ANTHROPIC_API_ENV)
            or os.environ.get(self.API_KEY_ENV)
            or os.environ.get(self.OAUTH_TOKEN_ENV)
        ):
            raise OSError(
                "Claude Code Harbor runs require credentials. "
                "Set ANTHROPIC_API_KEY, CLAUDE_CODE_API_KEY, or CLAUDE_CODE_OAUTH_TOKEN."
            )

    def extra_harbor_args(self) -> Iterable[str]:
        model_profile = self.SUPPORTED_MODELS[self.config.model.name]
        default_effort = model_profile.default_reasoning
        reasoning_effort = self.config.model.reasoning_effort or default_effort
        if not reasoning_effort:
            return []
        return ["--ak", "thinking_mode=adaptive", "--ak", f"effort={reasoning_effort}"]
