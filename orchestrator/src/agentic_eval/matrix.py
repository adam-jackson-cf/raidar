"""Configuration matrix for comparing harness/model combinations."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .harness.config import Agent, HarnessConfig, ModelTarget


class HarnessModelPair(BaseModel):
    """Explicit harness/model pairing."""

    harness: str = Field(description="Harness identifier (matches Agent enum values)")
    model: str = Field(description="Model string provider/name passed to Harbor")


class MatrixConfig(BaseModel):
    """Configuration for a matrix of evaluation runs."""

    runs: list[HarnessModelPair] = Field(
        min_length=1,
        description="List of harness/model pairs to execute",
    )
    task_path: str = Field(description="Path to task.yaml")
    executions_path: str = Field(default="executions", description="Path to execution outputs")


class MatrixEntry(BaseModel):
    """Single entry in the configuration matrix."""

    harness: str
    model: str

    def to_harness_config(self) -> HarnessConfig:
        """Convert to HarnessConfig."""
        return HarnessConfig(
            agent=Agent(self.harness),
            model=ModelTarget.from_string(self.model),
        )

    @property
    def workspace_suffix(self) -> str:
        """Generate unique workspace suffix for this entry."""
        model_safe = self.model.replace("/", "-")
        return f"{self.harness}_{model_safe}"


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return MatrixConfig.model_validate(data.get("matrix", data))


def generate_matrix_entries(config: MatrixConfig) -> list[MatrixEntry]:
    """Generate all combinations from a matrix configuration."""
    entries: list[MatrixEntry] = []
    for pair in config.runs:
        entries.append(
            MatrixEntry(
                harness=pair.harness,
                model=pair.model,
            )
        )
    return entries


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""
    return """# Evaluation matrix configuration
matrix:
  runs:
    - harness: codex-cli
      model: codex/gpt-5.2-high
    - harness: claude-code
      model: anthropic/claude-sonnet-4-5
  task_path: tasks/homepage-implementation/v001/task.yaml
  executions_path: executions
"""
