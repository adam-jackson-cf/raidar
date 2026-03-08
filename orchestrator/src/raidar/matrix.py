"""Configuration matrix for comparing agent/model combinations."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .agents.config import Agent, AgentRunConfig, ModelTarget


class AgentModelPair(BaseModel):
    """Explicit agent/model pairing."""

    agent: str = Field(description="Agent identifier (matches Agent enum values)")
    model: str = Field(description="Model string provider/name passed to Harbor")


class ExperimentConfig(BaseModel):
    """Experiment execution settings for every matrix pair."""

    timeout_sec: int = Field(gt=0, description="Scenario timeout in seconds for each experiment")
    repeats: int = Field(
        ge=1,
        description="Number of runs per scenario/agent/model/evaluation_profile pair",
    )
    repeat_parallel: int = Field(ge=1, description="Parallel workers within one experiment")
    retry_void: int = Field(ge=0, le=1, description="Retry budget for voided runs")


class MatrixConfig(BaseModel):
    """Configuration for a matrix of experiment runs."""

    runs: list[AgentModelPair] = Field(
        min_length=1,
        description="List of agent/model pairs to execute",
    )
    suite: ExperimentConfig = Field(description="Experiment execution settings")
    scenario_path: str = Field(description="Path to scenario.yaml")
    experiments_path: str = Field(default="experiments", description="Path to experiment outputs")


class MatrixEntry(BaseModel):
    """Single entry in the configuration matrix."""

    agent: str
    model: str

    def to_agent_config(self) -> AgentRunConfig:
        """Convert to AgentRunConfig."""
        return AgentRunConfig(
            agent=Agent(self.agent),
            model=ModelTarget.from_string(self.model),
        )

    @property
    def workspace_suffix(self) -> str:
        """Generate unique workspace suffix for this entry."""

        model_safe = self.model.replace("/", "-")
        return f"{self.agent}_{model_safe}"


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return MatrixConfig.model_validate(data.get("matrix", data))


def generate_matrix_entries(config: MatrixConfig) -> list[MatrixEntry]:
    """Generate all combinations from a matrix configuration."""

    return [MatrixEntry(agent=pair.agent, model=pair.model) for pair in config.runs]


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""

    return """# Experiment matrix configuration
matrix:
  suite:
    timeout_sec: 1800
    repeats: 3
    repeat_parallel: 1
    retry_void: 1
  runs:
    - agent: codex-cli
      model: codex/gpt-5.2-high
    - agent: claude-code
      model: anthropic/claude-sonnet-4-5
  scenario_path: scenarios/homepage-implementation/v001/scenario.yaml
  experiments_path: experiments
"""
