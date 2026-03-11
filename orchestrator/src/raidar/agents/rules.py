"""CLI-to-rule-file mapping and rule injection logic.

Borrowed from enaible's install.py SYSTEM_RULES pattern.
"""

import shutil
from pathlib import Path

from .config import Harness

# Mapping from harness id to expected rule filename
SYSTEM_RULES: dict[Harness, str] = {
    Harness.CLAUDE_CODE: "CLAUDE.md",
    Harness.CODEX_CLI: "AGENTS.md",
    Harness.COPILOT: "copilot-instructions.md",
    Harness.CURSOR: "user-rules-setting.md",
    Harness.GEMINI: "GEMINI.md",
    Harness.PI: "AGENTS.md",
}


def get_rule_filename(harness: Harness) -> str:
    """Get the rule filename for a given harness."""
    if harness not in SYSTEM_RULES:
        supported = [candidate.value for candidate in SYSTEM_RULES]
        raise ValueError(f"Unknown harness '{harness.value}'. Supported: {supported}")
    return SYSTEM_RULES[harness]


def inject_rules(
    scenario_rules_dir: Path,
    target_dir: Path,
    harness: Harness,
) -> Path:
    """Inject rule file for the specified harness into target directory.

    Args:
        scenario_rules_dir: Path to scenario rules directory
        target_dir: Path to workspace target directory
        harness: Harness id for rule-file selection

    Returns:
        Path to injected rule file
    """
    target_filename = get_rule_filename(harness)
    if not scenario_rules_dir.exists():
        raise FileNotFoundError(f"Rules directory not found: {scenario_rules_dir}")

    source_file = scenario_rules_dir / target_filename
    if not source_file.exists():
        raise FileNotFoundError(
            f"Expected rules file {target_filename} for harness {harness.value} "
            f"in {scenario_rules_dir}"
        )

    target_path = target_dir / target_filename
    shutil.copy2(source_file, target_path)
    return target_path
