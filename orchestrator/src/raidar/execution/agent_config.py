"""The Harbor ``AgentConfig`` for one arm (plan WS-D D1/D2).

A real arm runs Harbor's installed agent for its name (``codex`` or
``claude-code``) pinned to the version the Python pack image bakes in, so no
per-trial install happens (WS-I), with the arm's compiled condition applied
(WS-C). A dev/perf run may replace the agent with Harbor's no-LLM ``oracle``
or ``nop`` agent; the compiled per-step reset still runs, and the job records
the override so its results are never compared with real arms.

A condition's session wrappers (``continue-on-stall@1``) are nested around the
native agent, inside the condition's workflow wrapper when it has one
(``raidar.components.runtime.session_chain``); the native agent's pin and auth are read
through every level of wrapping.
"""

from __future__ import annotations

from typing import Literal

from harbor.models.trial.config import AgentConfig

from raidar.agents.codex_auth import resolve_codex_auth
from raidar.agents.config import Harness
from raidar.components import HARBOR_AGENT_NAMES, CompiledCondition
from raidar.components.runtime.session_chain import (
    apply_session_wrappers,
    condition_session_wrappers,
)
from raidar.packs.python.harness import harbor_agent_kwargs
from raidar.packs.python.models import PackSpec
from raidar.spec.canonical import digest_json
from raidar.spec.schema import AgentEntry

AgentOverride = Literal["oracle", "nop"]
AGENT_OVERRIDES: tuple[AgentOverride, ...] = ("oracle", "nop")
CODEX_AUTH_ENV = "CODEX_AUTH_JSON_PATH"


def arm_agent_config(
    agent: AgentEntry,
    compiled: CompiledCondition,
    pack: PackSpec,
    *,
    agent_timeout_sec: int,
    override: AgentOverride | None = None,
) -> AgentConfig:
    """Harbor agent config for an arm; raises ``HarnessPinError`` off the pack pin."""
    if override is not None:
        return AgentConfig(name=override, override_timeout_sec=float(agent_timeout_sec))
    kwargs: dict[str, object] = dict(
        harbor_agent_kwargs(pack, agent.harness.value, configured=agent.version)
    )
    if agent.reasoning_effort is not None:
        kwargs["reasoning_effort"] = agent.reasoning_effort
    base = AgentConfig(
        name=HARBOR_AGENT_NAMES[agent.harness],
        model_name=f"{agent.provider}/{agent.model}",
        kwargs=kwargs,
        override_timeout_sec=float(agent_timeout_sec),
    )
    wrapped = compiled.apply_to_agent_config(base)
    return apply_session_wrappers(wrapped, condition_session_wrappers(compiled))


def agent_digest(agent: AgentEntry, config: AgentConfig, override: AgentOverride | None) -> str:
    """Identity of what the arm runs; component inputs are covered by the condition digest."""
    return digest_json(
        {
            "agent": agent.model_dump(mode="json", exclude={"id"}),
            "harbor_agent": config.name or config.import_path,
            "version": config.kwargs.get("version"),
            "override": override,
        }
    )


def reported_harness_pin(config: AgentConfig) -> str | None:
    """The CLI version this agent must run: the pack pin of its native agent.

    A workflow wrapper (e.g. review-loop@1) carries the wrapped native agent
    in ``kwargs["inner_agent"]``; its pin is the inner agent's. ``None`` only
    for a no-LLM override, which runs no agent CLI.
    """
    name, kwargs = _native_agent(config)
    if name not in HARBOR_AGENT_NAMES.values():
        return None
    version = kwargs.get("version")
    return str(version) if version is not None else None


def _native_agent(config: AgentConfig) -> tuple[str | None, dict[str, object]]:
    """(name, kwargs) of the native agent, looking inside every wrapper around it."""
    name, kwargs = config.name, config.kwargs
    inner = kwargs.get("inner_agent")
    while name is None and isinstance(inner, dict):
        name, kwargs = inner.get("name"), inner.get("kwargs") or {}
        inner = kwargs.get("inner_agent")
    return name, kwargs


def with_host_auth(config: AgentConfig, harness: Harness) -> AgentConfig:
    """Point Harbor's codex agent at the host's file-backed session auth, if configured.

    Only the file path is passed (presence-checked); the credential itself is
    never read here. Without a session file Harbor falls back to OPENAI_API_KEY.
    A workflow wrapper (review-loop@1) gets the same env: it hands its env to
    every inner session, so reviewer and fixer sessions authenticate too.
    """
    name, _ = _native_agent(config)
    if name != HARBOR_AGENT_NAMES[Harness.CODEX_CLI] or harness is not Harness.CODEX_CLI:
        return config
    auth = resolve_codex_auth()
    if auth.resolved_mode != "chatgpt" or auth.auth_json_path is None:
        return config
    return config.model_copy(
        update={"env": {**config.env, CODEX_AUTH_ENV: str(auth.auth_json_path)}}
    )
