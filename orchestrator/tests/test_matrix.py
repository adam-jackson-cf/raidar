"""Tests for matrix generation."""

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
        assert config.model.model_name == "claude-sonnet-4-5"
        assert config.rules_variant == "minimal"


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_generates_all_combinations(self):
        """Should generate all combinations."""
        config = MatrixConfig(
            harnesses=["codex-cli", "claude-code"],
            models=["openai/gpt-4o"],
            rules_variants=["strict", "minimal"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        # 2 harnesses * 1 model * 2 rules = 4 entries
        assert len(entries) == 4

    def test_generates_correct_combinations(self):
        """Should generate correct harness/model/rules combinations."""
        config = MatrixConfig(
            harnesses=["codex-cli"],
            models=["openai/gpt-4o", "anthropic/claude-sonnet-4-5"],
            rules_variants=["strict"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2
        models = {e.model for e in entries}
        assert "openai/gpt-4o" in models
        assert "anthropic/claude-sonnet-4-5" in models

    def test_empty_config_generates_empty_list(self):
        """Empty config should generate empty list."""
        config = MatrixConfig(
            harnesses=[],
            models=["openai/gpt-4o"],
            rules_variants=["strict"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)
        assert len(entries) == 0

    def test_large_matrix_generation(self):
        """Should handle larger matrices."""
        config = MatrixConfig(
            harnesses=["codex-cli", "claude-code", "cursor"],
            models=["openai/gpt-4o", "anthropic/claude-sonnet-4-5"],
            rules_variants=["strict", "minimal", "none"],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        # 3 harnesses * 2 models * 3 rules = 18 entries
        assert len(entries) == 18
