"""The Harbor ``AgentConfig`` for one arm (plan WS-D D1/D2; design section 5.5).

A real arm runs the workflow runner (``raidar.subject.workflow.runner.WorkflowRunner``) with
the arm's workflow definition, around the native agent for the arm's adapter: the class its
adapter names (``HarborCliAdapter.native_agent_class``: ``IsolatedCodex`` for ``codex``,
``IsolatedClaudeCode`` for ``claude-code``; Harbor's agent with the agent definition's isolation
settings applied on every command) pinned to the version the Python
pack image bakes in, so no per-trial install happens (WS-I). The native agent's model,
reasoning level, pin and isolation settings are read through the runner's ``inner_agent``.
A dev/perf run may replace the agent with Harbor's no-LLM ``oracle`` or ``nop`` agent; the
per-step reset still runs, and the job records the override so its results are never
compared with real arms.
"""

from __future__ import annotations

from typing import Literal

from harbor.models.trial.config import AgentConfig, TrialConfig

from raidar.contract.canonical import digest_json
from raidar.design.experiment.arm import ResolvedArm
from raidar.subject.agent.config import HARBOR_AGENT_NAMES
from raidar.subject.agent.definition import harbor_adapter
from raidar.subject.environment.python.harness import harbor_agent_kwargs
from raidar.subject.environment.python.image import image_lock_hash
from raidar.subject.environment.python.pack_definition import PackSpec
from raidar.subject.workflow.inner import INNER_KWARG, InnerAgentSpec
from raidar.subject.workflow.runner import WorkflowRunner

AgentOverride = Literal["oracle", "nop"]
AGENT_OVERRIDES: tuple[AgentOverride, ...] = ("oracle", "nop")


def effective_agent_timeout_s(agent_timeout_sec: int) -> float:
    """The agent phase's real budget: the configured timeout scaled as Harbor scales it.

    Harbor multiplies an agent's ``override_timeout_sec`` by the trial's ``timeout_multiplier`` and
    ``agent_timeout_multiplier`` (their defaults here: Raidar sets neither), the same product the
    ``H4`` health check holds the phase to.
    """
    fields = TrialConfig.model_fields
    scale = float(fields["timeout_multiplier"].default or 1.0)
    return (
        float(agent_timeout_sec) * scale * float(fields["agent_timeout_multiplier"].default or 1.0)
    )


def _package_kwargs(arm: ResolvedArm, pack: PackSpec) -> dict[str, object]:
    """What a package workflow's runner needs beyond its definition: the locked packages, the
    primitives' code identities and the pack the trial's container is built from."""
    if not arm.workflow.stages:
        return {}
    return {
        "locked_packages": arm.packages,
        "primitives": arm.primitive_code,
        "pack": image_lock_hash(pack),
    }


def arm_agent_config(
    arm: ResolvedArm,
    pack: PackSpec,
    *,
    agent_timeout_sec: int,
    override: AgentOverride | None = None,
) -> AgentConfig:
    """Harbor agent config for an arm; raises ``HarnessPinError`` off the pack pin."""
    if override is not None:
        return AgentConfig(name=override, override_timeout_sec=float(agent_timeout_sec))
    agent = arm.agent
    kwargs: dict[str, object] = dict(
        harbor_agent_kwargs(pack, agent.harness.value, configured=agent.version)
    )
    if agent.reasoning is not None:
        kwargs["reasoning_effort"] = agent.reasoning
    adapter = harbor_adapter(agent.name)
    isolated = adapter.native_agent_class()
    kwargs.update(adapter.isolation_kwargs(agent.released.definition.settings))
    if arm.workflow.stages:
        kwargs["instructions_path"] = agent.released.definition.placement.instructions
    native = InnerAgentSpec(
        name=HARBOR_AGENT_NAMES[agent.harness], import_path=isolated.import_path(), kwargs=kwargs
    )
    return AgentConfig(
        import_path=WorkflowRunner.import_path(),
        model_name=f"{agent.provider}/{agent.model}",
        kwargs={
            INNER_KWARG: native.model_dump(mode="json"),
            "workflow": arm.workflow.ref,
            "definition": arm.definition.to_json(),
            "skills_placement": agent.released.definition.placement.skills,
            "phase_timeout_s": effective_agent_timeout_s(agent_timeout_sec),
            **_package_kwargs(arm, pack),
        },
        override_timeout_sec=float(agent_timeout_sec),
    )


def agent_digest(arm: ResolvedArm, config: AgentConfig, override: AgentOverride | None) -> str:
    """Identity of the agent the arm runs: its release, model, reasoning and what Harbor runs."""
    agent = arm.agent
    native_name, native_kwargs = native_agent(config)
    return digest_json(
        {
            "release": agent.released.ref,
            "model": agent.model,
            "reasoning": agent.reasoning,
            "harbor_agent": native_name,
            "version": native_kwargs.get("version"),
            "override": override,
        }
    )


def native_agent(config: AgentConfig) -> tuple[str | None, dict[str, object]]:
    """(name, kwargs) of the native agent: inside the workflow runner, or the config itself."""
    inner = config.kwargs.get(INNER_KWARG)
    if config.name is None and isinstance(inner, dict):
        return inner.get("name"), dict(inner.get("kwargs") or {})
    return config.name, dict(config.kwargs)


def reported_harness_pin(config: AgentConfig) -> str | None:
    """The CLI version this agent must run: the pack pin of its native agent.

    ``None`` only for a no-LLM override, which runs no agent CLI.
    """
    name, kwargs = native_agent(config)
    if name not in HARBOR_AGENT_NAMES.values():
        return None
    version = kwargs.get("version")
    return str(version) if version is not None else None
