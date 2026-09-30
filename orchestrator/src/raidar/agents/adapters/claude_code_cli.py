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
