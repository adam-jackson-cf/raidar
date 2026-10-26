"""The Harbor ``AgentConfig`` for one arm (plan WS-D D1/D2; design section 5.5).

A real arm runs the workflow runner (``raidar.subject.workflow.runner.WorkflowRunner``) with
the arm's workflow definition, around the native agent for the arm's adapter (``codex``, run
by ``raidar.subject.agent.isolated_codex.IsolatedCodex``: Harbor's codex agent with the agent
definition's isolation settings applied on every command) pinned to the version the Python
pack image bakes in, so no per-trial install happens (WS-I). The native agent's model,
reasoning level, pin and isolation settings are read through the runner's ``inner_agent``.
A dev/perf run may replace the agent with Harbor's no-LLM ``oracle`` or ``nop`` agent; the
per-step reset still runs, and the job records the override so its results are never
compared with real arms.
"""

from __future__ import annotations

from typing import Literal

from harbor.models.trial.config import AgentConfig

from raidar.contract.canonical import digest_json
from raidar.design.experiment.arm import ResolvedArm
from raidar.subject.agent.codex_auth import resolve_codex_auth
from raidar.subject.agent.config import HARBOR_AGENT_NAMES, AgentName, Harness
from raidar.subject.agent.isolated_codex import IsolatedCodex
from raidar.subject.environment.python.harness import harbor_agent_kwargs
from raidar.subject.environment.python.pack_definition import PackSpec
from raidar.subject.workflow.inner import INNER_KWARG, InnerAgentSpec
from raidar.subject.workflow.runner import WorkflowRunner

AgentOverride = Literal["oracle", "nop"]
AGENT_OVERRIDES: tuple[AgentOverride, ...] = ("oracle", "nop")
CODEX_AUTH_ENV = "CODEX_AUTH_JSON_PATH"
ISOLATED_AGENTS = {AgentName.CODEX: IsolatedCodex}
"""The Raidar class that runs each adapter's native agent with its definition's isolation."""


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
    isolated = ISOLATED_AGENTS.get(agent.name)
    if isolated is None:
        raise ValueError(f"adapter {agent.name.value!r} has no isolated native agent")
    settings = agent.released.definition.settings
    kwargs["features_off"] = list(settings.features_off)
    kwargs["bundled_skills"] = list(settings.bundled_skills)
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


def with_host_auth(config: AgentConfig, harness: Harness) -> AgentConfig:
    """Point Harbor's codex agent at the host's file-backed session auth, if configured.

    Only the file path is passed (presence-checked); the credential itself is
    never read here. Without a session file Harbor falls back to OPENAI_API_KEY.
    The workflow runner hands its env to every native turn, so every turn authenticates.
    """
    name, _ = native_agent(config)
    if name != HARBOR_AGENT_NAMES[Harness.CODEX_CLI] or harness is not Harness.CODEX_CLI:
        return config
    auth = resolve_codex_auth()
    if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
        return config
    return config.model_copy(
        update={"env": {**config.env, CODEX_AUTH_ENV: str(auth.auth_json_path)}}
    )
