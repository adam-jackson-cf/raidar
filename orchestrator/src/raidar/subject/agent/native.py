"""The public way to reach the agent adapters (``raidar.subject.agent.adapters`` is private).

Everything outside ``raidar.subject.agent`` selects an adapter, a native agent class or a
harness's host-side facts here, by ``Harness``: nothing imports a codex or claude module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from harbor.agents.base import BaseAgent

from raidar.subject.agent.adapters.base import HarnessAdapter
from raidar.subject.agent.adapters.harbor_cli import HarborCliAdapter
from raidar.subject.agent.adapters.registry import registry
from raidar.subject.agent.config import AgentSpec, Harness
from raidar.subject.agent.protocol import NativeAgent
from raidar.subject.agent.records import Credentials, StatePath


def resolve_adapter(spec: AgentSpec) -> HarnessAdapter:
    """The registered adapter for an ``AgentSpec``."""
    return registry.resolve(spec)


def adapter_class_for_harness(harness: Harness) -> type[HarnessAdapter]:
    """The registered adapter class of a harness (CLI discovery and validation)."""
    return registry.adapter_class(harness)


def _harbor(adapter: type[HarnessAdapter]) -> type[HarborCliAdapter]:
    if not issubclass(adapter, HarborCliAdapter):
        raise ValueError(f"adapter {adapter.__name__} is not a Harbor-installed agent adapter")
    return adapter


def _native(adapter: type[HarnessAdapter]) -> type[NativeAgent]:
    return cast(type[NativeAgent], _harbor(adapter).native_agent_class())


def native_agent_class(harness: Harness) -> type[BaseAgent]:
    """The Raidar class that runs a harness's Harbor agent; it implements ``NativeAgent``."""
    return _harbor(registry.adapter_class(harness)).native_agent_class()


def credentials(harness: Harness, env: Mapping[str, str]) -> Credentials:
    """Whether ``env`` can authenticate ``harness``, and where its session login is kept."""
    return _native(registry.adapter_class(harness)).credentials(env)


def state_paths() -> tuple[StatePath, ...]:
    """Every harness's persisted state the per-step reset restores, in registration order."""
    return tuple(
        path for adapter in registry.adapter_classes() for path in _native(adapter).state_paths()
    )
