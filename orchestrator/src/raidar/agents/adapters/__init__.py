"""Agent adapter implementations."""

from .base import AgentAdapter
from .claude_code_cli import ClaudeCodeCliAdapter
from .codex_cli import CodexCliAdapter
from .copilot_cli import CopilotCliAdapter
from .cursor_cli import CursorCliAdapter
from .default import HarborAgentAdapter
from .external_cli import ExternalCliAdapter
from .gemini_cli import GeminiCliAdapter
from .pi_cli import PiCliAdapter
from .registry import registry

__all__ = [
    "AgentAdapter",
    "ClaudeCodeCliAdapter",
    "CodexCliAdapter",
    "CopilotCliAdapter",
    "CursorCliAdapter",
    "GeminiCliAdapter",
    "PiCliAdapter",
    "ExternalCliAdapter",
    "HarborAgentAdapter",
    "registry",
]
