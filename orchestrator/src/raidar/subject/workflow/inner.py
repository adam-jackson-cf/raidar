"""The native agent a workflow runner drives: its spec and a factory for one turn's instance.

The runner is the Harbor agent of every real arm; the native Harbor installed agent (``codex``)
it wraps is named in its ``inner_agent`` kwarg with that agent's own kwargs (the pinned
``version``, ``reasoning_effort``, the agent definition's isolation settings). ``import_path``
names the Raidar class that runs it when the lab adapts Harbor's agent
(``raidar.subject.agent.adapters.isolated_codex.IsolatedCodex`` for ``codex``); ``name`` stays the
Harbor agent name either way. ``create_inner`` builds one instance for a turn, sharing the
runner's model, environment variables, logger, MCP servers and skills directory, and writing
its logs to the turn's directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.agents.options import AgentOptions
from harbor.models.trial.config import AgentConfig
from pydantic import BaseModel, ConfigDict, Field

from raidar.subject.workflow.definition import WorkflowDefinition
from raidar.subject.workflow.package_definition import PackageWorkflowDefinition

INNER_KWARG = "inner_agent"


class InnerAgentSpec(BaseModel):
    """The wrapped native agent: a Harbor agent ``name``, the class running it and its kwargs."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    import_path: str | None = None
    kwargs: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunnerOptions(AgentOptions):
    inner_agent: InnerAgentSpec
    workflow: str
    definition: WorkflowDefinition | PackageWorkflowDefinition
    skills_placement: str = ".agents/skills"
    phase_timeout_s: float | None = None
    locked_packages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    primitives: dict[str, str] = Field(default_factory=dict)
    pack: str = ""


def create_inner(
    runner: BaseAgent, spec: InnerAgentSpec, logs_dir: Path, *, model_name: str | None
) -> BaseAgent:
    """Build ``spec`` with ``runner``'s model, env, tools and logger, logging to ``logs_dir``."""
    return AgentFactory.create_agent_from_config(
        AgentConfig(
            name=None if spec.import_path else spec.name,
            import_path=spec.import_path,
            model_name=model_name,
            kwargs=spec.kwargs,
            env=runner.extra_env,
        ),
        logs_dir=logs_dir,
        logger=runner.logger,
        mcp_servers=runner.mcp_servers,
        skills_dir=runner.skills_dir,
        environment_logs_dir=runner.environment_logs_dir,
    )
