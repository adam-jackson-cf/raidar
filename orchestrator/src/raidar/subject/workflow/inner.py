"""The native agent a workflow runner drives: its spec and a factory for one turn's instance.

The runner is the Harbor agent of every real arm; the native Harbor installed agent (``codex``)
it wraps is named in its ``inner_agent`` kwarg with that agent's own kwargs (the pinned
``version``, ``reasoning_effort``). ``create_inner`` builds one instance for a turn, sharing the
runner's model, environment variables, logger, MCP servers and skills directory, and writing
its logs to the turn's directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.models.trial.config import AgentConfig
from pydantic import BaseModel, ConfigDict, Field

INNER_KWARG = "inner_agent"


class InnerAgentSpec(BaseModel):
    """The wrapped native agent: a Harbor agent ``name`` and its kwargs."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kwargs: dict[str, Any] = Field(default_factory=dict)


def create_inner(
    runner: BaseAgent, spec: InnerAgentSpec, logs_dir: Path, *, model_name: str | None
) -> BaseAgent:
    """Build ``spec`` with ``runner``'s model, env, tools and logger, logging to ``logs_dir``."""
    return AgentFactory.create_agent_from_config(
        AgentConfig(
            name=spec.name, model_name=model_name, kwargs=spec.kwargs, env=runner.extra_env
        ),
        logs_dir=logs_dir,
        logger=runner.logger,
        mcp_servers=runner.mcp_servers,
        skills_dir=runner.skills_dir,
        environment_logs_dir=runner.environment_logs_dir,
    )
