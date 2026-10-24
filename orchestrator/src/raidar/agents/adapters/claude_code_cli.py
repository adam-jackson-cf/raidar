"""Claude Code CLI harness adapter."""

from __future__ import annotations

from .harbor_cli import HarborCliAdapter, SupportedModelProfile


class ClaudeCodeCliAdapter(HarborCliAdapter):
    """Adapter enforcing Claude Code CLI harness + model pairing."""

    HARBOR_HARNESS_NAME = "claude-code"
    REQUIRED_PROVIDER = "anthropic"
    ADAPTER_LABEL = "Claude Code CLI adapter"
    CLI_ENV_VAR = "CLAUDE_CODE_CLI_PATH"
    DEFAULT_BINARY = "claude"
    SUPPORTED_MODELS: dict[str, SupportedModelProfile] = {
        "claude-opus-4-7": SupportedModelProfile(
            reasoning_levels=("low", "medium", "high", "xhigh", "max"),
        ),
        "claude-opus-4-6": SupportedModelProfile(
            reasoning_levels=("low", "medium", "high", "max"),
        ),
        "claude-sonnet-4-6": SupportedModelProfile(
            reasoning_levels=("low", "medium", "high", "max"),
        ),
        "claude-sonnet-4-5": SupportedModelProfile(),
        "claude-haiku-4-5": SupportedModelProfile(),
    }
