"""Configuration matrix definitions and job resolution."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agents.config import AgentSpec, Harness, ModelTarget
from .application.scenario_catalog import load_scenario
from .schemas.scenario import ScenarioDefinition


class AgentSpecInput(BaseModel):
    """Explicit harness/model pairing for one agent spec."""

    model_config = ConfigDict(extra="forbid")

    harness: str = Field(description="Harness identifier (matches Harness enum values)")
    provider: str = Field(description="Upstream model provider")
    model: str = Field(description="Upstream model identifier passed to Harbor")
    reasoning_effort: str | None = Field(
        default=None,
        description="Optional normalized reasoning/thinking effort",
    )


class ExperimentConfig(BaseModel):
    """Experiment execution settings for every matrix entry."""

    model_config = ConfigDict(extra="forbid")

    timeout_sec: int = Field(gt=0, description="Scenario timeout in seconds for each experiment")
    repeats: int = Field(
        ge=1,
        description="Number of runs per scenario/agent-spec/evaluation-profile pair",
    )
    repeat_parallel: int = Field(ge=1, description="Parallel workers within one experiment")
    retry_void: int = Field(ge=0, le=1, description="Retry budget for unscored runs")


class MatrixEntryInput(BaseModel):
    """Single executable matrix entry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable matrix entry identifier")
    scenario_revision: str = Field(description="Scenario revision directory under matrix.scenario")
    agent: AgentSpecInput = Field(description="AgentSpec to run for this scenario revision")


class MatrixConfig(BaseModel):
    """Configuration for a matrix of experiment runs."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable matrix identifier")
    scenario: Path = Field(description="Scenario root directory containing revision folders")
    experiment: ExperimentConfig = Field(description="Experiment execution settings")
    entries: list[MatrixEntryInput] = Field(
        min_length=1,
        description="Executable matrix entries",
    )

    @model_validator(mode="after")
    def _validate_entry_ids(self) -> "MatrixConfig":
        entry_ids = [entry.id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("matrix.entries contains duplicate entry ids")
        return self


class MatrixAgentSpec(BaseModel):
    """Single agent spec entry in the configuration matrix."""

    model_config = ConfigDict(extra="forbid")

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


@dataclass(frozen=True, slots=True)
class MatrixJob:
    """Resolved executable matrix job."""

    entry_id: str
    scenario_path: Path
    scenario: ScenarioDefinition
    agent: MatrixAgentSpec


def load_matrix_config(path: Path) -> MatrixConfig:
    """Load matrix configuration from YAML file."""

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return MatrixConfig.model_validate(data.get("matrix", data))


def matrix_entry_agent_spec(entry: MatrixEntryInput) -> MatrixAgentSpec:
    """Return the executable AgentSpec for a matrix entry."""

    agent = entry.agent
    return MatrixAgentSpec(
        harness=agent.harness,
        provider=agent.provider,
        model=agent.model,
        reasoning_effort=agent.reasoning_effort,
    )


def resolve_matrix_jobs(config: MatrixConfig, *, repo_root: Path) -> list[MatrixJob]:
    """Resolve a matrix config to executable jobs."""

    scenario_root = (
        config.scenario if config.scenario.is_absolute() else repo_root / config.scenario
    )
    if not scenario_root.is_dir():
        raise FileNotFoundError(f"Matrix scenario root does not exist: {scenario_root}")
    jobs: list[MatrixJob] = []
    for entry in config.entries:
        scenario_path = scenario_root / entry.scenario_revision / "scenario.yaml"
        if not scenario_path.is_file():
            raise FileNotFoundError(
                f"Matrix entry '{entry.id}' scenario revision does not exist: {scenario_path}"
            )
        scenario = load_scenario(scenario_path)
        jobs.append(
            MatrixJob(
                entry_id=entry.id,
                scenario_path=scenario_path,
                scenario=scenario,
                agent=matrix_entry_agent_spec(entry),
            )
        )
    return jobs


def create_example_matrix() -> str:
    """Create example matrix configuration YAML."""

    return """# Experiment matrix configuration
matrix:
  id: example-matrix
  scenario: scenarios/example-scenario
  experiment:
    timeout_sec: 1800
    repeats: 5
    repeat_parallel: 1
    retry_void: 1
  entries:
    - id: codex-gpt-5-4-high-v001
      scenario_revision: v001
      agent:
        harness: codex-cli
        provider: openai
        model: gpt-5.4
        reasoning_effort: high
    - id: claude-sonnet-4-5-v001
      scenario_revision: v001
      agent:
        harness: claude-code
        provider: anthropic
        model: claude-sonnet-4-5
"""
