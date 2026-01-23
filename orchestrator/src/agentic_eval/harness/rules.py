"""CLI-to-rule-file mapping and rule injection logic.

Borrowed from enaible's install.py SYSTEM_RULES pattern.
"""

import shutil
from pathlib import Path
from typing import Literal

# Mapping from CLI/agent name to expected rule filename
SYSTEM_RULES: dict[str, str] = {
    "claude-code": "CLAUDE.md",
    "codex-cli": "AGENTS.md",
    "copilot": "copilot-instructions.md",
    "cursor": "user-rules-setting.md",
    "gemini": "GEMINI.md",
    "pi": "AGENTS.md",
}


def get_rule_filename(agent: str) -> str:
    """Get the rule filename for a given agent."""
    if agent not in SYSTEM_RULES:
        raise ValueError(f"Unknown agent '{agent}'. Supported: {list(SYSTEM_RULES.keys())}")
    return SYSTEM_RULES[agent]


def inject_rules(
    task_rules_dir: Path,
    target_dir: Path,
    agent: str,
    variant: Literal["strict", "minimal", "none"],
) -> Path | None:
    """Inject rule file for the specified agent and variant into target directory.

    Args:
        task_rules_dir: Path to task's rules directory (e.g., tasks/homepage/rules/)
        target_dir: Path to scaffold target directory
        agent: Agent name (claude-code, codex, etc)
        variant: Rules variant (strict, minimal, none)

    Returns:
        Path to injected rule file, or None if variant is 'none'
    """
    target_filename = get_rule_filename(agent)
    variant_dir = task_rules_dir / variant

    if not variant_dir.exists():
        raise FileNotFoundError(f"Rules variant directory not found: {variant_dir}")

    # Find the source rule file (may have different name in source)
    source_file = variant_dir / target_filename
    if not source_file.exists():
        # Try finding any markdown file in the variant directory
        md_files = list(variant_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(f"No rule file found for {agent} in {variant_dir}")
        source_file = md_files[0]

    target_path = target_dir / target_filename
    shutil.copy2(source_file, target_path)
    return target_path
