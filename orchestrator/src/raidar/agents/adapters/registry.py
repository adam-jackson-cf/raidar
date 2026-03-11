"""Adapter registry wiring harnesses to their implementations."""

from __future__ import annotations

from ..config import AgentSpec, Harness
from .base import HarnessAdapter
from .claude_code_cli import ClaudeCodeCliAdapter
from .codex_cli import CodexCliAdapter
from .copilot_cli import CopilotCliAdapter
from .cursor_cli import CursorCliAdapter
from .gemini_cli import GeminiCliAdapter
from .pi_cli import PiCliAdapter

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


registry = AdapterRegistry()


# Default registrations for existing Harbor-native harnesses
registry.register(Harness.CLAUDE_CODE, ClaudeCodeCliAdapter)
registry.register(Harness.CODEX_CLI, CodexCliAdapter)
registry.register(Harness.GEMINI, GeminiCliAdapter)
# External CLI harnesses
registry.register(Harness.CURSOR, CursorCliAdapter)
registry.register(Harness.COPILOT, CopilotCliAdapter)
registry.register(Harness.PI, PiCliAdapter)
