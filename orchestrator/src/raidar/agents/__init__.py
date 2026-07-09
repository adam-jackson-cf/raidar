"""AgentSpec configuration and rule management."""

from .config import AgentSpec, Harness, ModelTarget
from .rules import get_rule_filename, inject_rules

__all__ = [
    "AgentSpec",
    "Harness",
    "ModelTarget",
    "get_rule_filename",
    "inject_rules",
]
