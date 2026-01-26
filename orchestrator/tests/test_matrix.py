"""Tests for matrix generation."""

import pytest
from pydantic import ValidationError

from agentic_eval.matrix import (
    MatrixConfig,
    MatrixEntry,
    generate_matrix_entries,
)


class TestMatrixEntry:
    """Test MatrixEntry functionality."""

    def test_workspace_suffix_generation(self):
        """Workspace suffix should be safe for filesystem."""
        entry = MatrixEntry(
            harness="codex-cli",
            model="openai/gpt-4o",
            rules_variant="strict",
        )
        suffix = entry.workspace_suffix
        assert "/" not in suffix
        assert "codex-cli" in suffix
        assert "gpt-4o" in suffix
        assert "strict" in suffix

    def test_to_harness_config(self):
        """Should convert to HarnessConfig correctly."""
        entry = MatrixEntry(
            harness="claude-code",
            model="anthropic/claude-sonnet-4-5",
            rules_variant="minimal",
        )
        config = entry.to_harness_config()
        assert config.agent.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == "claude-sonnet-4-5"
        assert config.rules_variant == "minimal"


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_generates_all_combinations(self):
        """Should generate all combinations."""
        config = MatrixConfig(
            runs=[
                {"harness": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"harness": "claude-code", "model": "anthropic/claude-sonnet-4-5"},
            ],
            rules_variants=["strict", "minimal"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        # 2 pairs * 2 rule variants = 4 entries
        assert len(entries) == 4

    def test_generates_correct_combinations(self):
        """Should generate correct harness/model/rules combinations."""
        config = MatrixConfig(
            runs=[
                {"harness": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"harness": "codex-cli", "model": "codex/gpt-5.1"},
            ],
            rules_variants=["strict"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2
        models = {e.model for e in entries}
        assert "codex/gpt-5.2-high" in models
        assert "codex/gpt-5.1" in models

    def test_empty_config_generates_empty_list(self):
        """Empty config should raise validation error."""
        try:
            MatrixConfig(
                runs=[],
                rules_variants=["strict"],
                task_path="task.yaml",
            )
        except ValidationError:
            assert True
        else:
            pytest.fail("MatrixConfig should require at least one run")

    def test_large_matrix_generation(self):
        """Should handle larger matrices."""
        config = MatrixConfig(
            runs=[
                {"harness": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"harness": "claude-code", "model": "anthropic/claude-sonnet-4-5"},
                {"harness": "cursor", "model": "openai/gpt-4o-mini"},
            ],
            rules_variants=["strict", "minimal", "none"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        # 3 pairs * 3 rules = 9 entries
        assert len(entries) == 9
