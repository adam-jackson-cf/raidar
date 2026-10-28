"""Native agents for tests, reached through the public agent API (never an adapter module)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.subject.agent.config import Harness
from raidar.subject.agent.native import native_agent_class

MODELS = {
    Harness.CODEX_CLI: ("openai/gpt-6-luna", "0.156.1"),
    Harness.CLAUDE_CODE: ("anthropic/claude-sonnet-4-6", "2.1.285"),
}

CodexNative: Any = native_agent_class(Harness.CODEX_CLI)
"""The class that runs Harbor's codex agent (a base for scripted agents)."""


def native_agent(harness: Harness, logs_dir: Path, **kwargs: Any) -> Any:
    """A native agent of ``harness`` logging to ``logs_dir``."""
    model, version = MODELS[harness]
    settings: dict[str, Any] = {
        "logs_dir": logs_dir,
        "model_name": model,
        "version": version,
        "reasoning_effort": "low",
    }
    return native_agent_class(harness)(**settings | kwargs)


def session_audit(harness: Harness, turn_dir: Path) -> dict[str, Any]:
    """The normalized ``session_audit`` of one turn directory."""
    return native_agent(harness, turn_dir).session_audit(turn_dir)
