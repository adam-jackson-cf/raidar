"""Harness rule-file injection logic."""

import shutil
from pathlib import Path

from raidar.harness import HarnessDefinitionError, harness_definition

from .config import Harness


def rule_filename_for_harness(harness: Harness | str) -> str | None:
    """Return the injected rule filename for a harness id, if known."""
    harness_id = harness.value if isinstance(harness, Harness) else harness
    try:
        return harness_definition(harness_id).rule_filename
    except HarnessDefinitionError:
        return None


def get_rule_filename(harness: Harness) -> str:
    """Get the rule filename for a given harness."""
    filename = rule_filename_for_harness(harness)
    if filename is None:
        raise ValueError(f"Unknown harness '{harness.value}'.")
    return filename


def injected_rules_path(workspace_dir: Path, harness: Harness | str) -> Path | None:
    """Return the injected rules path in a workspace, if present."""
    injected_rule_name = rule_filename_for_harness(harness)
    if not injected_rule_name:
        return None
    candidate = workspace_dir / injected_rule_name
    return candidate if candidate.exists() else None


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
