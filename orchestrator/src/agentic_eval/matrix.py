"""Configuration matrix for comparing harness/model/rules combinations."""

from itertools import product
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .harness.config import Agent, HarnessConfig, ModelConfig


class MatrixConfig(BaseModel):
    """Configuration for a matrix of evaluation runs."""

    harnesses: list[str] = Field(description="List of harnesses to test")
    models: list[str] = Field(description="List of models to test (provider/name)")
    rules_variants: list[Literal["strict", "minimal", "none"]] = Field(
        default=["strict", "minimal", "none"],
        description="List of rules variants to test",
    )
    task_path: str = Field(description="Path to task.yaml")
    scaffold_path: str = Field(default="scaffold", description="Path to scaffold template")
    workspace_base: str = Field(default="workspace", description="Base path for workspaces")
    results_path: str = Field(default="results", description="Path to store results")


class MatrixEntry(BaseModel):
    """Single entry in the configuration matrix."""

    harness: str
    model: str
    rules_variant: Literal["strict", "minimal", "none"]

    def to_harness_config(self) -> HarnessConfig:
        """Convert to HarnessConfig."""
        return HarnessConfig(
            agent=Agent(self.harness),
            model=ModelConfig.from_string(self.model),
            rules_variant=self.rules_variant,
        )

    @property
    def workspace_suffix(self) -> str:
        """Generate unique workspace suffix for this entry."""
        model_safe = self.model.replace("/", "-")
        return f"{self.harness}_{model_safe}_{self.rules_variant}"


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return MatrixConfig.model_validate(data.get("matrix", data))


def generate_matrix_entries(config: MatrixConfig) -> list[MatrixEntry]:
    """Generate all combinations from a matrix configuration.

    Args:
        config: Matrix configuration

    Returns:
        List of all harness/model/rules combinations
    """
    entries = []
    for harness, model, rules in product(config.harnesses, config.models, config.rules_variants):
        entries.append(
            MatrixEntry(
                harness=harness,
                model=model,
                rules_variant=rules,
            )
        )
    return entries


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""
    return """# Evaluation matrix configuration
matrix:
  harnesses:
    - codex
    - claude-code
  models:
    - openai/gpt-5
    - anthropic/claude-sonnet-4-5
  rules_variants:
    - strict
    - minimal
    - none
  task_path: tasks/homepage-implementation/task.yaml
  scaffold_path: scaffold
  workspace_base: workspace
  results_path: results
"""
