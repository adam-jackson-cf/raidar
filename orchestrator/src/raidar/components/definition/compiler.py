"""Component compiler: one condition x one harness -> a ``CompiledCondition``.

``compile_condition`` resolves every component use through the registry and
rejects the arm when any use is unknown, unsupported on the arm's harness,
invalid, or conflicts with another use. Nothing is skipped silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from harbor.models.task.config import MCPServerConfig
from pydantic import JsonValue

from raidar.components.definition.compiled import HARBOR_AGENT_NAMES, CompiledCondition, CompiledUse
from raidar.components.definition.contribution import (
    AgentWrapper,
    Contribution,
    ContributionConflictError,
    SupportFile,
    merge_native_config,
)
from raidar.components.definition.manifest import ComponentConfigError, ComponentUseContext
from raidar.components.definition.registry import ComponentRegistry, fingerprint
from raidar.components.runtime.setup_script import SETUP_SCRIPT_NAME
from raidar.spec.references import RegistryReferenceError
from raidar.spec.schema import AgentEntry, ConditionSpec, ExperimentSpec


class ComponentRejectedError(ValueError):
    """The arm's condition cannot be compiled for its harness; the arm is rejected."""

    def __init__(self, label: str, issues: list[str]) -> None:
        self.label = label
        self.issues = tuple(issues)
        lines = "\n".join(f"  {issue}" for issue in self.issues)
        super().__init__(f"{label}: rejected, {len(self.issues)} component issue(s)\n{lines}")


@dataclass
class _Merged:
    overlay: dict[str, bytes] = field(default_factory=dict)
    support_files: dict[str, SupportFile] = field(default_factory=dict)
    native_config: dict[str, JsonValue] = field(default_factory=dict)
    skills: list[Path] = field(default_factory=list)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    prompt_template: Path | None = None
    agent_wrapper: AgentWrapper | None = None

    def add(self, slug: str, contribution: Contribution) -> None:
        for path, content in contribution.overlay.items():
            if path in self.overlay or path == SETUP_SCRIPT_NAME:
                raise ContributionConflictError(f"workspace path {path!r} is set twice")
            self.overlay[path] = content
        for relative, support in contribution.support_files.items():
            self.support_files[f"{slug}/{relative}"] = support
        self.native_config = merge_native_config(self.native_config, contribution.native_config)
        self._add_skills(contribution.skills)
        self._add_mcp(contribution.mcp_servers)
        if contribution.prompt_template is not None:
            if self.prompt_template is not None:
                raise ContributionConflictError("more than one prompt template")
            self.prompt_template = contribution.prompt_template
        if contribution.agent_wrapper is not None:
            if self.agent_wrapper is not None:
                raise ContributionConflictError("more than one workflow wraps the agent")
            self.agent_wrapper = contribution.agent_wrapper

    def _add_skills(self, skills: tuple[Path, ...]) -> None:
        names = {path.name for path in self.skills}
        for path in skills:
            if path.name in names:
                raise ContributionConflictError(f"skill directory name {path.name!r} is used twice")
            names.add(path.name)
            self.skills.append(path)

    def _add_mcp(self, servers: tuple[MCPServerConfig, ...]) -> None:
        names = {server.name for server in self.mcp_servers}
        for server in servers:
            if server.name in names:
                raise ContributionConflictError(f"MCP server name {server.name!r} is used twice")
            names.add(server.name)
            self.mcp_servers.append(server)


def _compile_use(
    registry: ComponentRegistry, context: ComponentUseContext, ref: str
) -> tuple[CompiledUse, Contribution]:
    definition = registry.get(ref)
    if context.harness not in definition.manifest.supports:
        supported = ", ".join(harness.value for harness in definition.manifest.supports)
        raise ComponentConfigError(
            f"{ref} does not support harness {context.harness.value} (supports: {supported})"
        )
    contribution = definition.contribute(context)
    compiled = CompiledUse(context.slug, ref, definition, context, fingerprint(definition))
    return compiled, contribution


def _compile_uses(
    condition: ConditionSpec, agent: AgentEntry, base_dir: Path, registry: ComponentRegistry
) -> tuple[list[CompiledUse], _Merged, list[str]]:
    """Compile every use; collect all issues so the rejection lists each one."""
    uses: list[CompiledUse] = []
    merged = _Merged()
    issues: list[str] = []
    for index, use in enumerate(condition.components):
        name = use.use.split("@", 1)[0].replace("/", "-")
        context = ComponentUseContext(
            slug=f"{index:02d}-{name}",
            harness=agent.harness,
            base_dir=base_dir,
            files=use.files,
            with_=use.with_,
        )
        try:
            compiled, contribution = _compile_use(registry, context, use.use)
            merged.add(context.slug, contribution)
        except (RegistryReferenceError, ComponentConfigError, ContributionConflictError) as exc:
            issues.append(f"components.{index} ({use.use}): {exc}")
            continue
        uses.append(compiled)
    return uses, merged, issues


def compile_condition(
    condition: ConditionSpec,
    agent: AgentEntry,
    *,
    base_dir: Path,
    registry: ComponentRegistry | None = None,
) -> CompiledCondition:
    """Compile a condition for one agent's harness; raise ``ComponentRejectedError``."""
    label = f"condition {condition.id!r} on {agent.harness.value}"
    if agent.harness not in HARBOR_AGENT_NAMES:
        raise ComponentRejectedError(label, [f"no component mapping for {agent.harness.value}"])
    uses, merged, issues = _compile_uses(
        condition, agent, base_dir, registry or ComponentRegistry()
    )
    if issues:
        raise ComponentRejectedError(label, issues)
    manifests = [use.definition.manifest for use in uses]
    return CompiledCondition(
        condition_id=condition.id,
        harness=agent.harness,
        uses=tuple(uses),
        overlay=merged.overlay,
        support_files=merged.support_files,
        native_config=merged.native_config,
        skills=tuple(merged.skills),
        mcp_servers=tuple(merged.mcp_servers),
        prompt_template=merged.prompt_template,
        agent_wrapper=merged.agent_wrapper,
        network_egress=tuple(sorted({h for m in manifests for h in m.network.egress_hosts})),
        secrets=tuple(sorted({s for m in manifests for s in m.secrets})),
    )


def compile_arm(
    spec: ExperimentSpec,
    arm_id: str,
    *,
    base_dir: Path,
    registry: ComponentRegistry | None = None,
) -> CompiledCondition:
    arm = next((arm for arm in spec.arms if arm.id == arm_id), None)
    if arm is None:
        raise KeyError(f"unknown arm {arm_id!r}")
    return compile_condition(
        spec.condition(arm.condition), spec.agent(arm.agent), base_dir=base_dir, registry=registry
    )
