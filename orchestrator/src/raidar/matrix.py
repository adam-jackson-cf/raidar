"""Configuration matrix for comparing agent specs."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .agents.adapters.claude_code_cli import ClaudeCodeCliAdapter
from .agents.adapters.gemini_cli import GeminiCliAdapter
from .agents.config import AgentSpec, Harness, ModelTarget

MatrixSelector = Literal["all", "codex", "gemini", "claude"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]

CODEX_REASONING_EFFORTS: tuple[ReasoningEffort, ...] = ("low", "medium", "high", "xhigh")
CODEX_SELECTOR_MODEL_CATALOG: tuple[tuple[str, tuple[ReasoningEffort | None, ...]], ...] = (
    ("gpt-5.5", CODEX_REASONING_EFFORTS),
    ("gpt-5.2", ("low", "medium", "high")),
    ("gpt-5.3-codex-spark", CODEX_REASONING_EFFORTS),
    ("gpt-5.4", CODEX_REASONING_EFFORTS),
    ("gpt-5.4-mini", (None, "low")),
)


class AgentSpecInput(BaseModel):
    """Explicit harness/model pairing for one agent spec."""

    harness: str = Field(description="Harness identifier (matches Harness enum values)")
    provider: str = Field(description="Upstream model provider")
    model: str = Field(description="Upstream model identifier passed to Harbor")
    reasoning_effort: str | None = Field(
        default=None,
        description="Optional normalized reasoning/thinking effort",
    )


class ExperimentConfig(BaseModel):
    """Experiment execution settings for every matrix agent spec."""

    timeout_sec: int = Field(gt=0, description="Scenario timeout in seconds for each experiment")
    repeats: int = Field(
        ge=1,
        description="Number of runs per scenario/agent-spec/evaluation-profile pair",
    )
    repeat_parallel: int = Field(ge=1, description="Parallel workers within one experiment")
    retry_void: int = Field(ge=0, le=1, description="Retry budget for unscored runs")


class MatrixConfig(BaseModel):
    """Configuration for a matrix of experiment runs."""

    agents: list[AgentSpecInput] = Field(
        min_length=1,
        description="List of agent specs to execute",
    )
    experiment: ExperimentConfig = Field(description="Experiment execution settings")


class MatrixAgentSpec(BaseModel):
    """Single agent spec entry in the configuration matrix."""

    harness: str
    provider: str
    model: str
    reasoning_effort: str | None = None

    def to_agent_spec(self) -> AgentSpec:
        """Convert to AgentSpec."""
        return AgentSpec(
            harness=Harness(self.harness),
            model=ModelTarget(
                provider=self.provider,
                name=self.model,
                reasoning_effort=self.reasoning_effort,
            ),
        )

    @property
    def workspace_suffix(self) -> str:
        """Generate unique workspace suffix for this entry."""

        model_safe = f"{self.provider}-{self.model}"
        if self.reasoning_effort:
            return f"{self.harness}_{model_safe}_{self.reasoning_effort}"
        return f"{self.harness}_{model_safe}"


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return MatrixConfig.model_validate(data.get("matrix", data))


def generate_matrix_entries(config: MatrixConfig) -> list[MatrixAgentSpec]:
    """Generate all combinations from a matrix configuration."""

    return [
        MatrixAgentSpec(
            harness=spec.harness,
            provider=spec.provider,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
        )
        for spec in config.agents
    ]


def matrix_selector_choices() -> tuple[str, ...]:
    """Return supported on-the-fly matrix selectors."""

    return ("all", "codex", "gemini", "claude")


def _codex_selector_agent_specs() -> list[AgentSpecInput]:
    return [
        AgentSpecInput(
            harness=Harness.CODEX_CLI.value,
            provider="openai",
            model=model,
            reasoning_effort=reasoning_effort,
        )
        for model, reasoning_efforts in CODEX_SELECTOR_MODEL_CATALOG
        for reasoning_effort in reasoning_efforts
    ]


def _gemini_selector_agent_specs() -> list[AgentSpecInput]:
    return _model_selector_agent_specs(
        harness=Harness.GEMINI,
        provider="google",
        model_names=GeminiCliAdapter.SUPPORTED_MODELS,
    )


def _claude_selector_agent_specs() -> list[AgentSpecInput]:
    return _model_selector_agent_specs(
        harness=Harness.CLAUDE_CODE,
        provider="anthropic",
        model_names=ClaudeCodeCliAdapter.SUPPORTED_MODELS,
    )


def _model_selector_agent_specs(
    *,
    harness: Harness,
    provider: str,
    model_names,
) -> list[AgentSpecInput]:
    return [
        AgentSpecInput(harness=harness.value, provider=provider, model=model_name)
        for model_name in sorted(model_names)
    ]


def _selector_agent_specs(selector: MatrixSelector) -> list[AgentSpecInput]:
    codex_specs = _codex_selector_agent_specs()
    gemini_specs = _gemini_selector_agent_specs()
    claude_specs = _claude_selector_agent_specs()
    groups: dict[str, list[AgentSpecInput]] = {
        "codex": codex_specs,
        "gemini": gemini_specs,
        "claude": claude_specs,
        "all": [*codex_specs, *gemini_specs, *claude_specs],
    }
    return groups[selector]


def build_selected_matrix_config(
    *,
    selector: MatrixSelector,
    timeout_sec: int,
    repeats: int,
    repeat_parallel: int,
    retry_void: int,
) -> MatrixConfig:
    """Build a matrix config from a predefined selector."""

    return MatrixConfig(
        experiment=ExperimentConfig(
            timeout_sec=timeout_sec,
            repeats=repeats,
            repeat_parallel=repeat_parallel,
            retry_void=retry_void,
        ),
        agents=_selector_agent_specs(selector),
    )


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""

    return """# Experiment matrix configuration
matrix:
  experiment:
    timeout_sec: 1800
    repeats: 5
    repeat_parallel: 1
    retry_void: 1
  agents:
    - harness: codex-cli
      provider: openai
      model: gpt-5.4
      reasoning_effort: high
    - harness: claude-code
      provider: anthropic
      model: claude-sonnet-4-5
"""
