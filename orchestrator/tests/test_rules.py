"""Tests for harness rule selection and injection."""

from pathlib import Path

import pytest

from raidar.agents.config import Harness
from raidar.agents.rules import get_rule_filename, inject_rules


def test_get_rule_filename_uses_harness_enum() -> None:
    assert get_rule_filename(Harness.GEMINI) == "GEMINI.md"


def test_inject_rules_copies_expected_harness_file(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "GEMINI.md").write_text("gemini rules", encoding="utf-8")
    target_dir = tmp_path / "workspace"
    target_dir.mkdir()

    result = inject_rules(rules_dir, target_dir, Harness.GEMINI)

    assert result == target_dir / "GEMINI.md"
    assert result.read_text(encoding="utf-8") == "gemini rules"


def test_inject_rules_requires_expected_harness_file(tmp_path: Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "AGENTS.md").write_text("codex rules", encoding="utf-8")
    target_dir = tmp_path / "workspace"
    target_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="Expected rules file GEMINI.md"):
        inject_rules(rules_dir, target_dir, Harness.GEMINI)
