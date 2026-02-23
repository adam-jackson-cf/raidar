"""CLI-to-rule-file mapping and rule injection logic.

Borrowed from enaible's install.py SYSTEM_RULES pattern.
"""

import shutil
from pathlib import Path

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
) -> Path:
    """Inject rule file for the specified agent into target directory.

    Args:
        task_rules_dir: Path to task's rules directory
        target_dir: Path to scaffold target directory
        agent: Agent name (claude-code, codex, etc)

    Returns:
        Path to injected rule file
    """
    target_filename = get_rule_filename(agent)
    if not task_rules_dir.exists():
        raise FileNotFoundError(f"Rules directory not found: {task_rules_dir}")

    # Find the source rule file (may have different name in source)
    source_file = task_rules_dir / target_filename
    if not source_file.exists():
        # Try finding any markdown file in the rules directory
        md_files = list(task_rules_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(f"No rule file found for {agent} in {task_rules_dir}")
        source_file = md_files[0]

    target_path = target_dir / target_filename
    shutil.copy2(source_file, target_path)
    return target_path
