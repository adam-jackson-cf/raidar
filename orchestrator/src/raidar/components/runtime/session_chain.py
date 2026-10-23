"""Nested session wrappers: an inner agent that may itself be a wrapper (by import path).

A condition has at most one workflow that wraps the agent (the compiler's
rule, frozen with every component digest). A *session wrapper*
(``continue-on-stall@1``) wraps each session of the native agent instead, and
``raidar.execution.agent_config`` nests it between the workflow wrapper and
the native agent:

    workflow wrapper (guardrail-gate@3)  ->  session wrapper  ->  native agent

so ``inner_agent`` may name a Harbor agent (``name``) or a wrapper class
(``import_path``) whose own kwargs carry the next ``inner_agent``. The prompt
template (``prompt_template_path``) stays in the native agent's kwargs; the
outermost wrapper that renders the task takes it out of the chain
(``without_template``), so the task is rendered exactly once.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

from harbor.agents.base import BaseAgent
from harbor.agents.factory import AgentFactory
from harbor.models.trial.config import AgentConfig
from harbor.utils.templating import render_prompt_template
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raidar.components.definition.contribution import AgentWrapper
from raidar.components.definition.manifest import ComponentUseContext

if TYPE_CHECKING:
    from raidar.components.definition.compiled import CompiledCondition

PROMPT_TEMPLATE_KWARG = "prompt_template_path"
INNER_KWARG = "inner_agent"


class InnerAgentSpec(BaseModel):
    """The wrapped agent: a Harbor agent ``name`` or a wrapper ``import_path``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    import_path: str | None = Field(default=None, min_length=1)
    kwargs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.name is None) == (self.import_path is None):
            raise ValueError("inner_agent needs exactly one of 'name' and 'import_path'")
        return self

    def nested(self) -> InnerAgentSpec | None:
        """The wrapper's own inner agent (None for a native agent)."""
        if self.import_path is None:
            return None
        inner = self.kwargs.get(INNER_KWARG)
        return InnerAgentSpec.model_validate(inner) if isinstance(inner, dict) else None

    def native(self) -> InnerAgentSpec:
        """The native agent at the end of the chain."""
        nested = self.nested()
        return self if nested is None else nested.native()

    def template(self) -> str | None:
        """The prompt template the chain carries (on its native agent), if any."""
        value = self.native().kwargs.get(PROMPT_TEMPLATE_KWARG)
        return str(value) if value else None

    def without_template(self) -> InnerAgentSpec:
        """The same chain with the native agent's prompt template removed."""
        nested = self.nested()
        if nested is None:
            kwargs = {k: v for k, v in self.kwargs.items() if k != PROMPT_TEMPLATE_KWARG}
        else:
            kwargs = {**self.kwargs, INNER_KWARG: nested.without_template().model_dump()}
        return self.model_copy(update={"kwargs": kwargs})


def render_task(spec: InnerAgentSpec, instruction: str) -> str:
    """The instruction rendered with the chain's prompt template (unchanged without one)."""
    template = spec.template()
    return render_prompt_template(Path(template), instruction) if template else instruction


def create_inner(
    wrapper: BaseAgent, spec: InnerAgentSpec, logs_dir: Path, *, model_name: str | None
) -> BaseAgent:
    """Build ``spec`` (template removed) with ``wrapper``'s model, env, tools and logs."""
    chain = spec.without_template()
    return AgentFactory.create_agent_from_config(
        AgentConfig(
            name=chain.name,
            import_path=chain.import_path,
            model_name=model_name,
            kwargs=chain.kwargs,
            env=dict(wrapper._extra_env),
        ),
        logs_dir=logs_dir,
        logger=wrapper.logger,
        mcp_servers=wrapper.mcp_servers,
        skills_dir=wrapper.skills_dir,
        environment_logs_dir=wrapper.environment_logs_dir,
    )


def nest_session_wrapper(
    native: dict[str, Any], import_path: str, kwargs: dict[str, Any]
) -> dict[str, Any]:
    """``{import_path, kwargs: {..., inner_agent: native}}``: a wrapper around ``native``."""
    return {"import_path": import_path, "kwargs": {**kwargs, INNER_KWARG: native}}


@runtime_checkable
class SessionWrappingComponent(Protocol):
    """A component that wraps each session of the native agent (``session_wrapper``)."""

    def session_wrapper(self, use: ComponentUseContext) -> AgentWrapper: ...


def condition_session_wrappers(compiled: CompiledCondition) -> tuple[AgentWrapper, ...]:
    """The session wrappers of a compiled condition's uses, in condition order."""
    return tuple(
        use.definition.session_wrapper(use.context)
        for use in compiled.uses
        if isinstance(use.definition, SessionWrappingComponent)
    )


def apply_session_wrappers(config: AgentConfig, wrappers: Sequence[AgentWrapper]) -> AgentConfig:
    """``config`` with each session wrapper nested around its native agent.

    The first wrapper is the outermost of them; all sit inside the condition's
    workflow wrapper when it has one (``config.import_path`` with an
    ``inner_agent``), else the outermost becomes the arm's agent.
    """
    if not wrappers:
        return config
    if config.import_path is not None:
        native = dict(config.kwargs[INNER_KWARG])
    else:
        native = {"name": config.name, "kwargs": dict(config.kwargs)}
    for wrapper in reversed(wrappers):
        native = nest_session_wrapper(native, wrapper.import_path, dict(wrapper.kwargs))
    if config.import_path is not None:
        return config.model_copy(update={"kwargs": {**config.kwargs, INNER_KWARG: native}})
    return config.model_copy(
        update={"name": None, "import_path": native["import_path"], "kwargs": native["kwargs"]}
    )
