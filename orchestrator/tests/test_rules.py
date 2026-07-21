"""Tests for harness rule selection and injection."""

from pathlib import Path

import pytest

from raidar.agents import rules
from raidar.agents.config import Harness
from raidar.agents.rules import (
    get_rule_filename,
    inject_rules,
    injected_rules_path,
    rule_filename_for_harness,
)


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


def test_rule_lookup_and_injection_fail_closed(monkeypatch, tmp_path: Path) -> None:
    assert rule_filename_for_harness("unknown") is None
    assert injected_rules_path(tmp_path, "unknown") is None

    monkeypatch.setattr(rules, "rule_filename_for_harness", lambda _harness: None)
    with pytest.raises(ValueError, match="Unknown harness"):
        get_rule_filename(Harness.GEMINI)

    monkeypatch.undo()
    with pytest.raises(FileNotFoundError, match="Rules directory not found"):
        inject_rules(tmp_path / "missing", tmp_path, Harness.GEMINI)
