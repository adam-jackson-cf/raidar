"""AgentSpec configuration and rule management."""

from .config import AgentSpec, Harness, ModelTarget
from .rules import SYSTEM_RULES, get_rule_filename, inject_rules

__all__ = [
    "AgentSpec",
    "Harness",
    "ModelTarget",
    "SYSTEM_RULES",
    "get_rule_filename",
    "inject_rules",
]
