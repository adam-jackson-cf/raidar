"""Adapter registry wiring agents to their implementations."""

from __future__ import annotations

from typing import Callable, Dict, Type

from ..config import Agent, HarnessConfig
from .base import HarnessAdapter
from .codex_cli import CodexCliAdapter
from .copilot_cli import CopilotCliAdapter
from .cursor_cli import CursorCliAdapter
from .default import HarborHarnessAdapter
from .pi_cli import PiCliAdapter

AdapterFactory = Callable[[HarnessConfig], HarnessAdapter]


class AdapterRegistry:
    """Simple registry mapping agents to adapter factories."""

    def __init__(self) -> None:
        self._factories: Dict[Agent, AdapterFactory] = {}

    def register(self, agent: Agent, factory: AdapterFactory) -> None:
        self._factories[agent] = factory

    def resolve(self, config: HarnessConfig) -> HarnessAdapter:
        if config.agent not in self._factories:
            raise ValueError(f"No adapter registered for agent {config.agent.value}")
        return self._factories[config.agent](config)


registry = AdapterRegistry()


# Default registrations for existing Harbor-native harnesses
registry.register(Agent.CLAUDE_CODE, lambda cfg: HarborHarnessAdapter(cfg))
registry.register(Agent.CODEX_CLI, lambda cfg: CodexCliAdapter(cfg))
registry.register(Agent.GEMINI, lambda cfg: HarborHarnessAdapter(cfg))
registry.register(Agent.OPENHANDS, lambda cfg: HarborHarnessAdapter(cfg))
# External CLI harnesses
registry.register(Agent.CURSOR, lambda cfg: CursorCliAdapter(cfg))
registry.register(Agent.COPILOT, lambda cfg: CopilotCliAdapter(cfg))
registry.register(Agent.PI, lambda cfg: PiCliAdapter(cfg))
