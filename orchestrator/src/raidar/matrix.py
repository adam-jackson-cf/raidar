"""Configuration matrix for comparing agent specs."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .agents.config import AgentSpec, Harness, ModelTarget


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


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""

    return """# Experiment matrix configuration
matrix:
  experiment:
    timeout_sec: 1800
    repeats: 3
    repeat_parallel: 1
    retry_void: 1
  agents:
    - harness: codex-cli
      model: codex/gpt-5.4-high
    - harness: claude-code
      model: anthropic/claude-sonnet-4-5
"""
