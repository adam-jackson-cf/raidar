"""An arm, resolved: its agent release, model and reasoning, and its workflow release.

``resolve_arm`` loads both releases (``raidar.subject.releases``: only the release the tree
holds, at its registered hash) and checks the agent entry's model and reasoning against the
agent definition. ``ResolvedArm.identity`` is what the lock records per arm: the
agent release and its CLI pin, the model and reasoning, the workflow release, the workflow's
resolved content hash (a package workflow's folds in its packages), each package's release,
digest and stage-check proof, and one code digest per primitive the arm runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

from raidar_contract.canonical import digest_json
from raidar_contract.releases import ReleaseError
from raidar_contract.workflow import PackageWorkflowDefinition

from raidar.design.experiment.agent import AgentResolutionError, ResolvedAgent, resolve_agent
from raidar.design.experiment.schema import ExperimentSpec
from raidar.subject.draft import drafting
from raidar.subject.releases import ReleasedWorkflow, released_workflow
from raidar.subject.workflow.identity import package_contracts, primitive_code, spike_code
from raidar.subject.workflow.loading import AnyWorkflow
from raidar.subject.workflow.package_runtime import (
    PackageEvidence,
    evidence_issues,
    load_evidence,
)


@dataclass(frozen=True)
class ResolvedArm:
    arm_id: str
    agent: ResolvedAgent
    workflow: ReleasedWorkflow
    evidence: Mapping[str, PackageEvidence] = field(default_factory=dict)

    @property
    def definition(self) -> AnyWorkflow:
        return self.workflow.definition

    @cached_property
    def packages(self) -> dict[str, dict[str, object]]:
        """``{package: {release, sha256, installed, check}}``: each package's release, its
        ``stage-tree/1`` digest and the stage-check proof (installed tree per architecture)."""
        return {
            stage.release.name: {
                "release": str(stage.release),
                "sha256": stage.package.digest,
                **self.evidence[stage.release.name].entry(),
            }
            for stage in self.workflow.stages
        }

    @cached_property
    def primitive_code(self) -> dict[str, str]:
        """The code identity of every primitive the arm runs (spike stage types until phase 5)."""
        stages = self.workflow.stages
        if not stages and not isinstance(self.definition, PackageWorkflowDefinition):
            return spike_code(self.definition.enabled_stages, self.agent.name)
        contracts = package_contracts(stage.package for stage in stages)
        return primitive_code(contracts, self.agent.name)

    def identity(self) -> dict[str, object]:
        """The arm's full identity (agent + workflow + packages + primitive code), as locked.

        ``capabilities`` is the agent's capability declaration: pooling and smoke admission
        refuse arms whose declarations differ.
        """
        agent = self.agent
        definition = agent.released.definition
        body: dict[str, object] = {
            "agent": agent.entry.id,
            "agent_release": agent.released.ref,
            "adapter": agent.name.value,
            "provider": agent.provider,
            "model": agent.model,
            "reasoning": agent.reasoning,
            "tool": definition.tool.model_dump(),
            "capabilities": definition.capabilities.model_dump(),
            "workflow": self.workflow.ref,
            "workflow_sha256": self.workflow.digest,
            "packages": self.packages,
            "primitive_code": self.primitive_code,
        }
        return {**body, "digest": digest_json(body)}


class ArmResolutionError(ValueError):
    """An arm's releases cannot be resolved, or its agent entry does not fit its release."""


def workflow_evidence(workflow: ReleasedWorkflow) -> dict[str, PackageEvidence]:
    """The stage-check proof of each package; a normal arm lacking it is refused (a draft's
    missing proof is recorded as absent)."""
    evidence = {stage.release.name: load_evidence(stage) for stage in workflow.stages}
    if not drafting():
        missing = [
            issue
            for stage in workflow.stages
            for issue in evidence_issues(stage, evidence[stage.release.name])
        ]
        if missing:
            raise ArmResolutionError("; ".join(missing))
    return evidence


def resolve_arm(spec: ExperimentSpec, arm_id: str) -> ResolvedArm:
    """Resolve one arm of a validated spec; ``ArmResolutionError`` when a release fails.

    A normal arm needs each package's stage-check proof; under draft resolution (smoke only)
    the working tree resolves and missing proof is recorded as absent.
    """
    arm = spec.arm(arm_id)
    try:
        agent = resolve_agent(spec.agent(arm.agent))
    except AgentResolutionError as exc:
        raise ArmResolutionError(str(exc)) from exc
    try:
        workflow = released_workflow(arm.workflow)
        evidence = workflow_evidence(workflow)
    except (ReleaseError, ArmResolutionError) as exc:
        raise ArmResolutionError(f"arm {arm_id!r}: {exc}") from exc
    return ResolvedArm(arm_id, agent, workflow, evidence)
