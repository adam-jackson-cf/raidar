"""Configuration matrix for comparing agent specs."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .agents.adapters.claude_code_cli import ClaudeCodeCliAdapter
from .agents.adapters.codex_cli import CodexCliAdapter
from .agents.adapters.gemini_cli import GeminiCliAdapter
from .agents.config import AgentSpec, Harness, ModelTarget

MatrixSelector = Literal["all", "codex", "gemini", "claude"]


class AgentSpecInput(BaseModel):
    """Explicit harness/model pairing for one agent spec."""

    harness: str = Field(description="Harness identifier (matches Harness enum values)")
    model: str = Field(description="Model string provider/name passed to Harbor")


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
    model: str

    def to_agent_spec(self) -> AgentSpec:
        """Convert to AgentSpec."""
        return AgentSpec(
            harness=Harness(self.harness),
            model=ModelTarget.from_string(self.model),
        )

    @property
    def workspace_suffix(self) -> str:
        """Generate unique workspace suffix for this entry."""

        model_safe = self.model.replace("/", "-")
        return f"{self.harness}_{model_safe}"


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return MatrixConfig.model_validate(data.get("matrix", data))


def generate_matrix_entries(config: MatrixConfig) -> list[MatrixAgentSpec]:
    """Generate all combinations from a matrix configuration."""

    return [MatrixAgentSpec(harness=spec.harness, model=spec.model) for spec in config.agents]


def matrix_selector_choices() -> tuple[str, ...]:
    """Return supported on-the-fly matrix selectors."""

    return ("all", "codex", "gemini", "claude")


def _selector_agent_specs(selector: MatrixSelector) -> list[AgentSpecInput]:
    codex_specs = [
        AgentSpecInput(harness=Harness.CODEX_CLI.value, model=f"codex/{model_name}")
        for model_name in sorted(CodexCliAdapter.MODEL_ALIAS_MAP)
    ]
    gemini_specs = [
        AgentSpecInput(harness=Harness.GEMINI.value, model=f"google/{model_name}")
        for model_name in sorted(GeminiCliAdapter.SUPPORTED_MODELS)
    ]
    claude_specs = [
        AgentSpecInput(harness=Harness.CLAUDE_CODE.value, model=f"anthropic/{model_name}")
        for model_name in sorted(ClaudeCodeCliAdapter.SUPPORTED_MODELS)
    ]
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
      model: codex/gpt-5.4-high
    - harness: claude-code
      model: anthropic/claude-sonnet-4-5
"""
