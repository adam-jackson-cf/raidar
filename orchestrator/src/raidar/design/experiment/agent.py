"""An experiment's agent entry, resolved against its agent release.

The release fixes the adapter, the CLI pin and the models and reasoning levels an entry may
name; this module checks the entry against it and exposes the facts a trial runs with.
"""

from __future__ import annotations

from dataclasses import dataclass

from raidar_contract.agent import AgentName
from raidar_contract.releases import ReleaseError

from raidar.design.experiment.schema import AgentEntry
from raidar.subject.agent.config import Harness
from raidar.subject.agent.definition import harness_of, provider_of
from raidar.subject.releases import ReleasedAgent, released_agent


class AgentResolutionError(ValueError):
    """An agent release cannot be resolved, or its entry does not fit the release."""


def entry_issues(agent: AgentEntry, released: ReleasedAgent) -> list[tuple[str, str]]:
    """``(field, message)`` for a model or reasoning level the agent definition does not offer."""
    definition = released.definition
    model = definition.models.get(agent.model)
    if model is None:
        offered = ", ".join(sorted(definition.models))
        return [("model", f"{released.ref} offers models {offered}; got {agent.model!r}")]
    if agent.reasoning is None:
        if model.reasoning:
            return [
                (
                    "reasoning",
                    f"{agent.model} on {released.ref} needs one of {list(model.reasoning)}",
                )
            ]
        return []
    if agent.reasoning not in model.reasoning:
        allowed = list(model.reasoning) or "none"
        return [
            (
                "reasoning",
                f"{agent.model} on {released.ref} allows reasoning {allowed}; "
                f"got {agent.reasoning!r}",
            )
        ]
    return []


@dataclass(frozen=True)
class ResolvedAgent:
    entry: AgentEntry
    released: ReleasedAgent

    @property
    def name(self) -> AgentName:
        """The Harbor agent the adapter drives (``codex``)."""
        return self.released.definition.adapter

    @property
    def harness(self) -> Harness:
        return harness_of(self.released.definition)

    @property
    def provider(self) -> str:
        return provider_of(self.released.definition)

    @property
    def model(self) -> str:
        """The model id passed to the CLI."""
        return self.released.definition.models[self.entry.model].canonical

    @property
    def reasoning(self) -> str | None:
        return self.entry.reasoning

    @property
    def version(self) -> str:
        """The agent CLI pin."""
        return self.released.definition.tool.version


def resolve_agent(entry: AgentEntry) -> ResolvedAgent:
    try:
        released = released_agent(entry.release)
    except ReleaseError as exc:
        raise AgentResolutionError(f"agent {entry.id!r}: {exc}") from exc
    issues = entry_issues(entry, released)
    if issues:
        raise AgentResolutionError(f"agent {entry.id!r}: {'; '.join(m for _, m in issues)}")
    return ResolvedAgent(entry, released)
