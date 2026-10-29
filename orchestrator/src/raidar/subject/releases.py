"""Released agents and workflows: ``name@N`` resolved against ``lab/releases.yaml``.

Only the version the tree holds resolves: the register's latest version of the name, whose
registered hash must equal the definition's content hash now. An edited definition with no
new release, an unregistered name or an archived version is refused, so a lock always names
exactly the bytes it ran. An agent or workflow reference is ``name@N`` and a stage reference is
``ns/name@N`` (the namespace is required): a bare name, ``latest``, a branch, a tag or a path
never parses.

A package workflow's hash folds in each package's release, digest and effective params
(``subject.stages`` resolves them), so releasing a changed package makes every workflow that uses
it need its own new release. ``draft`` resolution (``subject.draft``, smoke experiments only)
resolves what the working tree holds without the register: an unregistered or edited package or
workflow, never an agent. The register check ``make quality`` runs is ``subject.release_check``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raidar.contract.paths import REPO_ROOT, agents_root, releases_file, workflows_root
from raidar.contract.references import (
    RefKind,
    RegistryRef,
    RegistryReferenceError,
    ResolvedReference,
)
from raidar.contract.releases import (
    Release,
    ReleaseError,
    ReleaseKind,
    ReleaseRegister,
    changed_message,
    load_register,
    resolve_registered,
)
from raidar.subject.agent.definition import AgentDefinition, AgentDefinitionError, load_agent
from raidar.subject.draft import drafting
from raidar.subject.stages import resolve_stages
from raidar.subject.workflow.loading import WORKFLOW_ERRORS, AnyWorkflow, load_any_workflow
from raidar.subject.workflow.package_checks import ResolvedStage
from raidar.subject.workflow.package_definition import (
    PackageWorkflowDefinition,
    package_workflow_digest,
)

DEFINITION_ERRORS = (AgentDefinitionError, *WORKFLOW_ERRORS)


@dataclass(frozen=True)
class ReleasedAgent:
    release: Release
    definition: AgentDefinition

    @property
    def ref(self) -> str:
        return str(self.release)


@dataclass(frozen=True)
class ReleasedWorkflow:
    """A workflow release with resolved packages folded into its identity."""

    release: Release
    definition: AnyWorkflow
    stages: tuple[ResolvedStage, ...] = ()
    draft: bool = False

    @property
    def ref(self) -> str:
        return str(self.release)

    @property
    def digest(self) -> str:
        """The workflow's identity: what a release registers and the lock records."""
        return workflow_digest(self.definition, self.stages)


def workflow_digest(definition: AnyWorkflow, stages: tuple[ResolvedStage, ...]) -> str:
    """The definition's content hash, folding packages for ``schema: 2``."""
    if isinstance(definition, PackageWorkflowDefinition):
        return package_workflow_digest(definition, [stage.entry for stage in stages])
    return definition.digest


def released_agent(text: str, *, repo_root: Path = REPO_ROOT) -> ReleasedAgent:
    """The agent release ``text``; ``ReleaseError`` unless it is the tree's current release."""
    register = load_register(releases_file(repo_root))
    release, registered = resolve_registered(register, ReleaseKind.AGENT, text)
    try:
        definition = load_agent(agents_root(repo_root), release.name)
    except AgentDefinitionError as exc:
        raise ReleaseError(str(exc)) from exc
    if definition.digest != registered:
        raise ReleaseError(changed_message(release, str(registered), definition.digest))
    return ReleasedAgent(release, definition)


def _workflow(
    name: str, repo_root: Path, register: ReleaseRegister, *, draft: bool
) -> tuple[AnyWorkflow, tuple[ResolvedStage, ...]]:
    try:
        definition = load_any_workflow(workflows_root(repo_root), name)
    except WORKFLOW_ERRORS as exc:
        raise ReleaseError(str(exc)) from exc
    if not isinstance(definition, PackageWorkflowDefinition):
        return definition, ()
    return definition, resolve_stages(definition, repo_root, register, draft=draft)


def released_workflow(
    text: str, *, repo_root: Path = REPO_ROOT, draft: bool | None = None
) -> ReleasedWorkflow:
    """The workflow release ``text``; ``ReleaseError`` unless it is the tree's current release.

    With ``draft`` the working tree resolves without the register (smoke experiments only).
    """
    draft = drafting() if draft is None else draft
    register = load_register(releases_file(repo_root))
    release, registered = resolve_registered(register, ReleaseKind.WORKFLOW, text, draft=draft)
    definition, stages = _workflow(release.name, repo_root, register, draft=draft)
    released = ReleasedWorkflow(release, definition, stages, draft)
    if not draft and released.digest != registered:
        raise ReleaseError(changed_message(release, str(registered), released.digest))
    return released


def tree_workflow_digest(name: str, repo_root: Path, register: ReleaseRegister) -> str:
    """A workflow's identity as the tree holds it (packages read from the tree, not the register:
    an unreleased package is its own register-check entry)."""
    definition, stages = _workflow(name, repo_root, register, draft=True)
    return workflow_digest(definition, stages)


@dataclass(frozen=True)
class AgentResolver:
    """The lock's ``agent`` resolver: digest, metadata and the RT5 binding (harness + pin)."""

    repo_root: Path = REPO_ROOT

    @property
    def kind(self) -> RefKind:
        return RefKind.AGENT

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        try:
            agent = released_agent(str(ref), repo_root=self.repo_root)
        except ReleaseError as exc:
            raise RegistryReferenceError(str(exc)) from exc
        definition = agent.definition
        return ResolvedReference(
            ref=ref,
            digest=definition.digest,
            metadata={
                "adapter": definition.adapter.value,
                "tool_version": definition.tool.version,
                "capabilities": definition.capabilities.model_dump(),
            },
            binding={"harness": definition.harness.value, "tool": definition.tool.model_dump()},
        )


@dataclass(frozen=True)
class WorkflowResolver:
    """The lock's ``workflow`` resolver: digest and the enabled stages (types or packages)."""

    repo_root: Path = REPO_ROOT

    @property
    def kind(self) -> RefKind:
        return RefKind.WORKFLOW

    def resolve(self, ref: RegistryRef, *, base_dir: Path) -> ResolvedReference:
        del base_dir
        try:
            workflow = released_workflow(str(ref), repo_root=self.repo_root)
        except ReleaseError as exc:
            raise RegistryReferenceError(str(exc)) from exc
        stages = list(workflow.definition.enabled_stages)
        return ResolvedReference(ref=ref, digest=workflow.digest, metadata={"stages": stages})
