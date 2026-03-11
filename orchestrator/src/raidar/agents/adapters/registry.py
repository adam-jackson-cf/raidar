"""Adapter registry wiring harnesses to their implementations."""

from __future__ import annotations

from collections.abc import Callable

from ..config import AgentSpec, Harness
from .base import HarnessAdapter
from .claude_code_cli import ClaudeCodeCliAdapter
from .codex_cli import CodexCliAdapter
from .copilot_cli import CopilotCliAdapter
from .cursor_cli import CursorCliAdapter
from .gemini_cli import GeminiCliAdapter
from .pi_cli import PiCliAdapter

AdapterFactory = Callable[[AgentSpec], HarnessAdapter]
AdapterType = type[HarnessAdapter]


class AdapterRegistry:
    """Simple registry mapping harnesses to adapter factories."""

    def __init__(self) -> None:
        self._factories: dict[Harness, AdapterFactory] = {}

    def register(self, harness: Harness, factory: AdapterFactory) -> None:
        self._factories[harness] = factory

    def resolve(self, spec: AgentSpec) -> HarnessAdapter:
        if spec.harness not in self._factories:
            raise ValueError(f"No adapter registered for harness {spec.harness.value}")
        return self._factories[spec.harness](spec)

    def adapter_class(self, harness: Harness) -> AdapterType:
        factory = self._factories.get(harness)
        if factory is None:
            raise ValueError(f"No adapter registered for harness {harness.value}")
        if not isinstance(factory, type) or not issubclass(factory, HarnessAdapter):
            raise TypeError(
                f"Registered factory for harness {harness.value} is not an adapter class"
            )
        return factory


registry = AdapterRegistry()


# Default registrations for existing Harbor-native harnesses
registry.register(Harness.CLAUDE_CODE, ClaudeCodeCliAdapter)
registry.register(Harness.CODEX_CLI, CodexCliAdapter)
registry.register(Harness.GEMINI, GeminiCliAdapter)
# External CLI harnesses
registry.register(Harness.CURSOR, CursorCliAdapter)
registry.register(Harness.COPILOT, CopilotCliAdapter)
registry.register(Harness.PI, PiCliAdapter)
