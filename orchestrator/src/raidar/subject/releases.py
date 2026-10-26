"""Released agents and workflows: ``name@N`` resolved against ``lab/releases.yaml``.

Only the version the tree holds resolves: the register's latest version of the name, whose
registered hash must equal the definition's content hash now. An edited definition with no
new release, an unregistered name or an archived version is refused, so a lock always names
exactly the bytes it ran.

``release_issues`` is the register check ``make quality`` runs: every definition in the tree
is registered at its current hash, every registered name is defined, and every agent's CLI pin
equals the pin of each runtime pack that bakes its harness in (the agent pin is the source).
"""

from __future__ import annotations

from collections.abc import Callable
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
    load_register,
)
from raidar.subject.agent.definition import (
    AgentDefinition,
    AgentDefinitionError,
    agent_names,
    load_agent,
)
from raidar.subject.environment import available_runtimes, load_runtime_pack
from raidar.subject.workflow.definition import (
    WorkflowDefinition,
    WorkflowDefinitionError,
    load_workflow,
    workflow_names,
)

DEFINITION_ERRORS = (AgentDefinitionError, WorkflowDefinitionError)


@dataclass(frozen=True)
class ReleasedAgent:
    release: Release
    definition: AgentDefinition

    @property
    def ref(self) -> str:
        return str(self.release)


@dataclass(frozen=True)
class ReleasedWorkflow:
    release: Release
    definition: WorkflowDefinition

    @property
    def ref(self) -> str:
        return str(self.release)


def _check_release[T: (AgentDefinition, WorkflowDefinition)](
    kind: ReleaseKind,
    text: str,
    load: Callable[[str], T],
    register: ReleaseRegister,
) -> tuple[Release, T]:
    release = Release.parse(text)
    registered = register.of(kind).get(release)
    if registered is None:
        raise ReleaseError(f"{kind.value} release {text} is not in lab/releases.yaml")
    latest = register.latest(kind, release.name)
    if latest != release:
        raise ReleaseError(
            f"{text} is not the {kind.value} release the tree holds ({latest}); "
            "earlier releases live in archive tags"
        )
    try:
        definition = load(release.name)
    except DEFINITION_ERRORS as exc:
        raise ReleaseError(str(exc)) from exc
    if definition.digest != registered:
        raise ReleaseError(
            f"{release.name} changed since {text} was released (registered {registered[:12]}, "
            f"now {definition.digest[:12]}): register {release.name}@{release.version + 1}"
        )
    return release, definition


def released_agent(text: str, *, repo_root: Path = REPO_ROOT) -> ReleasedAgent:
    """The agent release ``text``; ``ReleaseError`` unless it is the tree's current release."""
    root = agents_root(repo_root)
    register = load_register(releases_file(repo_root))
    release, definition = _check_release(
        ReleaseKind.AGENT, text, lambda name: load_agent(root, name), register
    )
    return ReleasedAgent(release, definition)


def released_workflow(text: str, *, repo_root: Path = REPO_ROOT) -> ReleasedWorkflow:
    """The workflow release ``text``; ``ReleaseError`` unless it is the tree's current release."""
    root = workflows_root(repo_root)
    register = load_register(releases_file(repo_root))
    release, definition = _check_release(
        ReleaseKind.WORKFLOW, text, lambda name: load_workflow(root, name), register
    )
    return ReleasedWorkflow(release, definition)


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
    """The lock's ``workflow`` resolver: digest and the enabled stage types."""

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
        definition = workflow.definition
        return ResolvedReference(
            ref=ref,
            digest=definition.digest,
            metadata={"stages": list(definition.enabled_stages)},
        )


def _registration_issues(
    kind: ReleaseKind,
    names: list[str],
    load: Callable[[str], AgentDefinition | WorkflowDefinition],
    register: ReleaseRegister,
) -> list[str]:
    issues = []
    for name in names:
        try:
            digest = load(name).digest
        except DEFINITION_ERRORS as exc:
            issues.append(str(exc))
            continue
        latest = register.latest(kind, name)
        if latest is None:
            issues.append(f"{kind.value}/{name} is not released: register {name}@1: {digest}")
        elif register.of(kind)[latest] != digest:
            issues.append(
                f"{kind.value}/{name} changed since {latest}: register "
                f"{name}@{latest.version + 1}: {digest}"
            )
    issues.extend(
        f"{kind.value} release name {name!r} is registered but not defined in the tree"
        for name in sorted(register.names(kind) - set(names))
    )
    return issues


def _pin_issues(agents: list[AgentDefinition]) -> list[str]:
    issues = []
    for runtime in available_runtimes():
        pack = load_runtime_pack(runtime)
        for agent in agents:
            pin = pack.image.harnesses.get(agent.harness.value)
            if pin is None:
                continue
            baked = (pin.npm_package, pin.executable, pin.version)
            pinned = (agent.tool.package, agent.tool.binary, agent.tool.version)
            if baked != pinned:
                issues.append(
                    f"agents/{agent.name}: tool {pinned} differs from pack {pack.id} "
                    f"image.harnesses.{agent.harness.value} {baked}; the agent pin is the source"
                )
    return issues


def release_issues(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every register, definition and pin problem in the tree (empty when consistent)."""
    try:
        register = load_register(releases_file(repo_root))
    except ReleaseError as exc:
        return [str(exc)]
    a_root, w_root = agents_root(repo_root), workflows_root(repo_root)
    issues = _registration_issues(
        ReleaseKind.AGENT, agent_names(a_root), lambda name: load_agent(a_root, name), register
    )
    issues += _registration_issues(
        ReleaseKind.WORKFLOW,
        workflow_names(w_root),
        lambda name: load_workflow(w_root, name),
        register,
    )
    agents = []
    for name in agent_names(a_root):
        try:
            agents.append(load_agent(a_root, name))
        except AgentDefinitionError:
            continue  # already reported above
    return issues + _pin_issues(agents)
