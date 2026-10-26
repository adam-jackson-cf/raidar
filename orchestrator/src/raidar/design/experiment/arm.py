"""An arm, resolved: its agent release, model and reasoning, and its workflow release.

``resolve_arm`` loads both releases (``raidar.subject.releases``: only the release the tree
holds, at its registered hash) and checks the agent entry's model and reasoning against the
agent definition. ``ResolvedArm.identity`` is what the lock records per arm (design 5.4): the
agent release and its CLI pin, the model and reasoning, the workflow release, the workflow's
resolved content hash and one stage code digest per enabled stage type.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from raidar.contract.canonical import digest_json
from raidar.contract.releases import ReleaseError
from raidar.design.experiment.schema import AgentEntry, ExperimentSpec
from raidar.subject.agent.config import AgentName, Harness
from raidar.subject.releases import (
    ReleasedAgent,
    ReleasedWorkflow,
    released_agent,
    released_workflow,
)
from raidar.subject.workflow.definition import WorkflowDefinition
from raidar.subject.workflow.stages import stage_code_digests


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
        return self.released.definition.harness

    @property
    def provider(self) -> str:
        return self.released.definition.provider

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


@dataclass(frozen=True)
class ResolvedArm:
    arm_id: str
    agent: ResolvedAgent
    workflow: ReleasedWorkflow

    @property
    def definition(self) -> WorkflowDefinition:
        return self.workflow.definition

    @cached_property
    def stage_code(self) -> dict[str, str]:
        return stage_code_digests(self.definition.enabled_stages, self.agent.name)

    def identity(self) -> dict[str, object]:
        """The arm's full identity (agent + workflow + stage code), as the lock records it."""
        agent = self.agent
        body: dict[str, object] = {
            "agent": agent.entry.id,
            "agent_release": agent.released.ref,
            "adapter": agent.name.value,
            "provider": agent.provider,
            "model": agent.model,
            "reasoning": agent.reasoning,
            "tool": agent.released.definition.tool.model_dump(),
            "workflow": self.workflow.ref,
            "workflow_sha256": self.definition.digest,
            "stage_code": self.stage_code,
        }
        return {**body, "digest": digest_json(body)}


class ArmResolutionError(ValueError):
    """An arm's releases cannot be resolved, or its agent entry does not fit its release."""


def resolve_agent(entry: AgentEntry) -> ResolvedAgent:
    try:
        released = released_agent(entry.release)
    except ReleaseError as exc:
        raise ArmResolutionError(f"agent {entry.id!r}: {exc}") from exc
    issues = entry_issues(entry, released)
    if issues:
        raise ArmResolutionError(f"agent {entry.id!r}: {'; '.join(m for _, m in issues)}")
    return ResolvedAgent(entry, released)


def resolve_arm(spec: ExperimentSpec, arm_id: str) -> ResolvedArm:
    """Resolve one arm of a validated spec; ``ArmResolutionError`` when a release fails."""
    arm = spec.arm(arm_id)
    agent = resolve_agent(spec.agent(arm.agent))
    try:
        workflow = released_workflow(arm.workflow)
    except ReleaseError as exc:
        raise ArmResolutionError(f"arm {arm_id!r}: {exc}") from exc
    return ResolvedArm(arm_id, agent, workflow)
