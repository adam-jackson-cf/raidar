"""Agent definitions as the orchestrator runs them: the portable schema plus its adapter.

``raidar_contract.agent`` owns the pure schema and its content hash. A definition is only usable
here when a Harbor-installed adapter exists for its ``adapter`` and supports every model and
reasoning level it lists; ``load_agent`` refuses any other definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar_contract.agent import AgentDefinitionError, agent_path
from raidar_contract.references import Release

from raidar.subject.agent.adapters.harbor_cli import HarborCliAdapter
from raidar.subject.agent.config import Harness, agent_cli
from raidar.subject.agent.native import adapter_class_for_harness
from raidar.subject.agent.schema import AgentDefinition, AgentName
from raidar.subject.agent.schema import load_agent as load_lab_agent


def harbor_adapter(name: AgentName) -> type[HarborCliAdapter]:
    """The adapter class of a Harbor-installed agent; refuse any other adapter kind."""
    adapter = adapter_class_for_harness(agent_cli(name))
    if not issubclass(adapter, HarborCliAdapter):
        raise ValueError(f"adapter {name.value!r} is not a Harbor-installed agent adapter")
    return adapter


def harness_of(definition: AgentDefinition) -> Harness:
    """The CLI install id (the runtime pack's harness key)."""
    return agent_cli(definition.adapter)


def provider_of(definition: AgentDefinition) -> str:
    """The model provider the definition's adapter requires."""
    return harbor_adapter(definition.adapter).REQUIRED_PROVIDER


def _unsupported(definition: AgentDefinition) -> str | None:
    """Why the adapter cannot run ``definition`` (``None``: it can)."""
    adapter = harbor_adapter(definition.adapter)
    for key, entry in definition.models.items():
        profile = adapter.SUPPORTED_MODELS.get(entry.canonical)
        if profile is None:
            return f"models.{key}: {adapter.ADAPTER_LABEL} has no model {entry.canonical!r}"
        unsupported = sorted(set(entry.reasoning) - set(profile.reasoning_levels))
        if unsupported:
            return (
                f"models.{key}: {adapter.ADAPTER_LABEL} allows reasoning "
                f"{list(profile.reasoning_levels)} for {entry.canonical!r}, not {unsupported}"
            )
    return None


def load_agent(root: Path, name: str) -> AgentDefinition:
    """The valid ``<root>/<name>.yaml`` whose adapter runs every model it lists."""
    definition = load_lab_agent(root, name)
    reason = _unsupported(definition)
    if reason is not None:
        raise AgentDefinitionError(f"{agent_path(root, name)}: invalid agent definition: {reason}")
    return definition


@dataclass(frozen=True)
class ReleasedAgent:
    release: Release
    definition: AgentDefinition

    @property
    def ref(self) -> str:
        return str(self.release)
