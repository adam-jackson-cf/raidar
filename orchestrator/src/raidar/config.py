"""Centralized configuration using pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class LLMAsJudgeSettings(BaseSettings):
    """LLM-as-judge metric configuration."""

    model_config = SettingsConfigDict(env_prefix="EVAL_LLM_AS_JUDGE__")

    model: str = Field(
        default="gpt-5.5",
        description="Codex CLI model for llm-as-judge evaluations",
    )
    reasoning_effort: str = Field(
        default="low",
        description="Codex CLI model reasoning effort for llm-as-judge evaluations",
    )
    codex_auth_mode: str = Field(
        default="chatgpt",
        description="Codex auth mode for llm-as-judge evaluations",
    )
    max_tokens: int = Field(default=1500, description="Max tokens for judge response")
    max_source_chars: int = Field(
        default=10000,
        description="Max source code characters to send to judge",
    )
    max_retries: int = Field(default=2, description="Max retries for LLM calls")


class VerificationStabilitySettings(BaseSettings):
    """Verification stability scoring parameters."""

    model_config = SettingsConfigDict(env_prefix="EVAL_VERIFICATION_STABILITY__")

    max_gate_failures: int = Field(
        default=4,
        description="Gate failures divisor for score calculation",
    )
    repeat_penalty: float = Field(default=0.2, description="Score penalty per repeat failure")


class GateWatcherSettings(BaseSettings):
    """Gate watcher configuration."""

    model_config = SettingsConfigDict(env_prefix="EVAL_GATE__")

    max_failures: int = Field(default=3, description="Max failures before termination")
    max_output_length: int = Field(default=2000, description="Max output length before truncation")


class VisualSettings(BaseSettings):
    """Visual comparison settings."""

    model_config = SettingsConfigDict(env_prefix="EVAL_VISUAL__")

    odiff_threshold: float = Field(
        default=0.03,
        description="Anti-aliasing tolerance for odiff (lower is stricter)",
    )
    similarity_threshold: float = Field(default=0.95, description="Default similarity threshold")


class ResourceEfficiencySettings(BaseSettings):
    """Resource efficiency scoring settings for valid runs."""

    model_config = SettingsConfigDict(env_prefix="EVAL_RESOURCE_EFFICIENCY__")

    max_uncached_tokens: int = Field(
        default=300_000,
        gt=0,
        description="Token count that maps to maximum token penalty",
    )
    max_commands: int = Field(
        default=20,
        gt=0,
        description="Command count that maps to maximum command penalty",
    )
    max_failed_commands: int = Field(
        default=6,
        gt=0,
        description="Failed command count that maps to maximum failure penalty",
    )
    max_extra_verification_rounds: int = Field(
        default=3,
        gt=0,
        description="Extra verification rounds before max penalty",
    )
    max_repeat_failures: int = Field(
        default=3,
        gt=0,
        description="Repeated verification failures before max penalty",
    )
    token_weight: float = Field(default=0.35, ge=0, le=1)
    command_weight: float = Field(default=0.15, ge=0, le=1)
    failure_weight: float = Field(default=0.25, ge=0, le=1)
    verification_round_weight: float = Field(default=0.15, ge=0, le=1)
    repeat_failure_weight: float = Field(default=0.10, ge=0, le=1)


class EvalSettings(BaseSettings):
    """Root configuration for the evaluation system."""

    model_config = SettingsConfigDict(env_prefix="EVAL_", env_nested_delimiter="__")

    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    llm_as_judge: LLMAsJudgeSettings = Field(default_factory=LLMAsJudgeSettings)
    verification_stability: VerificationStabilitySettings = Field(
        default_factory=VerificationStabilitySettings
    )
    gate: GateWatcherSettings = Field(default_factory=GateWatcherSettings)
    visual: VisualSettings = Field(default_factory=VisualSettings)
    resource_efficiency: ResourceEfficiencySettings = Field(
        default_factory=ResourceEfficiencySettings
    )


settings = EvalSettings()
