"""Application-facing adapter resolution."""

from __future__ import annotations

from raidar.agents.config import AgentSpec, Harness

from .base import HarnessAdapter
from .registry import registry


def resolve_adapter(spec: AgentSpec) -> HarnessAdapter:
    """Resolve the registered adapter for an AgentSpec."""

    return registry.resolve(spec)


def adapter_class_for_harness(harness: Harness) -> type[HarnessAdapter]:
    """Return the registered adapter class for CLI discovery."""

    return registry.adapter_class(harness)
