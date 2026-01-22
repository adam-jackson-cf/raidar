"""Centralized configuration using pydantic-settings.

All configurable values for the agentic evaluation system.
Values can be overridden via environment variables with EVAL_ prefix.

Example:
    EVAL_LLM_JUDGE__MODEL="openai/gpt-4o"
    EVAL_TIMEOUTS__BUILD=180
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoringWeights(BaseSettings):
    """Scoring dimension weights (must sum to 1.0)."""

    model_config = SettingsConfigDict(env_prefix="EVAL_WEIGHTS__")

    functional: float = Field(default=0.40, description="Functional tests weight")
    compliance: float = Field(default=0.25, description="Compliance checks weight")
    visual: float = Field(default=0.20, description="Visual regression weight")
    efficiency: float = Field(default=0.15, description="Efficiency/recovery weight")


class TimeoutSettings(BaseSettings):
    """Timeout values in seconds for various operations."""

    model_config = SettingsConfigDict(env_prefix="EVAL_TIMEOUTS__")

    build: int = Field(default=120, description="Build command timeout")
    typecheck: int = Field(default=60, description="Type check timeout")
    test: int = Field(default=120, description="Test suite timeout")
    gate: int = Field(default=60, description="Verification gate timeout")
    screenshot: int = Field(default=60, description="Screenshot capture timeout")
    image_compare: int = Field(default=30, description="Image comparison timeout")
    command_default: int = Field(default=60, description="Default command timeout")


class LLMJudgeSettings(BaseSettings):
    """LLM judge configuration."""

    model_config = SettingsConfigDict(env_prefix="EVAL_LLM_JUDGE__")

    model: str = Field(
        default="anthropic/claude-sonnet-4-20250514",
        description="LLM model for judge evaluations",
    )
    max_tokens: int = Field(default=200, description="Max tokens for judge response")
    max_source_chars: int = Field(
        default=10000,
        description="Max source code characters to send to judge",
    )
    max_retries: int = Field(default=2, description="Max retries for LLM calls")


class EfficiencySettings(BaseSettings):
    """Efficiency scoring parameters."""

    model_config = SettingsConfigDict(env_prefix="EVAL_EFFICIENCY__")

    max_gate_failures: int = Field(
        default=4,
        description="Gate failures divisor for score calculation",
    )
    repeat_penalty: float = Field(
        default=0.2,
        description="Score penalty per repeat failure",
    )


class GateWatcherSettings(BaseSettings):
    """Gate watcher configuration."""

    model_config = SettingsConfigDict(env_prefix="EVAL_GATE__")

    max_failures: int = Field(
        default=3,
        description="Max failures before termination",
    )
    max_output_length: int = Field(
        default=2000,
        description="Max output length before truncation",
    )


class VisualSettings(BaseSettings):
    """Visual comparison settings."""

    model_config = SettingsConfigDict(env_prefix="EVAL_VISUAL__")

    odiff_threshold: float = Field(
        default=0.1,
        description="Anti-aliasing tolerance for odiff",
    )
    similarity_threshold: float = Field(
        default=0.95,
        description="Default similarity threshold",
    )


class EvalSettings(BaseSettings):
    """Root configuration for the evaluation system.

    All settings can be overridden via environment variables with EVAL_ prefix.
    Nested settings use double underscore: EVAL_TIMEOUTS__BUILD=180
    """

    model_config = SettingsConfigDict(
        env_prefix="EVAL_",
        env_nested_delimiter="__",
    )

    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    llm_judge: LLMJudgeSettings = Field(default_factory=LLMJudgeSettings)
    efficiency: EfficiencySettings = Field(default_factory=EfficiencySettings)
    gate: GateWatcherSettings = Field(default_factory=GateWatcherSettings)
    visual: VisualSettings = Field(default_factory=VisualSettings)


# Singleton instance
settings = EvalSettings()
