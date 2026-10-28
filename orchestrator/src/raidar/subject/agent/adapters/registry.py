"""Adapter registry wiring harnesses to their implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Harness
from .base import HarnessAdapter
from .claude_code_cli import ClaudeCodeCliAdapter
from .codex_cli import CodexCliAdapter

if TYPE_CHECKING:
    from ..config import AgentSpec

AdapterType = type[HarnessAdapter]


class AdapterRegistry:
    """Simple registry mapping harnesses to adapter classes."""

    def __init__(self) -> None:
        self._adapters: dict[Harness, AdapterType] = {}

    def register(self, harness: Harness, adapter_class: AdapterType) -> None:
        self._adapters[harness] = adapter_class

    def resolve(self, spec: AgentSpec) -> HarnessAdapter:
        if spec.harness not in self._adapters:
            raise ValueError(f"No adapter registered for harness {spec.harness.value}")
        return self._adapters[spec.harness](spec)

    def adapter_class(self, harness: Harness) -> AdapterType:
        adapter_class = self._adapters.get(harness)
        if adapter_class is None:
            raise ValueError(f"No adapter registered for harness {harness.value}")
        return adapter_class

    def adapter_classes(self) -> tuple[AdapterType, ...]:
        """Every registered adapter class, in registration order."""
        return tuple(self._adapters.values())


registry = AdapterRegistry()


registry.register(Harness.CODEX_CLI, CodexCliAdapter)
registry.register(Harness.CLAUDE_CODE, ClaudeCodeCliAdapter)
