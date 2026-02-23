"""Tests for matrix generation."""

import pytest
from pydantic import ValidationError

from raidar.matrix import (
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
        )
        suffix = entry.workspace_suffix
        assert "/" not in suffix
        assert "codex-cli" in suffix
        assert "gpt-4o" in suffix

    def test_to_harness_config(self):
        """Should convert to HarnessConfig correctly."""
        entry = MatrixEntry(
            harness="claude-code",
            model="anthropic/claude-sonnet-4-5",
        )
        config = entry.to_harness_config()
        assert config.agent.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == "claude-sonnet-4-5"

    @pytest.mark.parametrize(
        ("model_name"),
        ("claude-opus-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"),
    )
    def test_to_harness_config_for_requested_claude_models(self, model_name: str):
        """Should parse requested Claude model variants."""
        entry = MatrixEntry(
            harness="claude-code",
            model=f"anthropic/{model_name}",
        )
        config = entry.to_harness_config()
        assert config.agent.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == model_name

    @pytest.mark.parametrize(
        ("model_name"),
        ("gemini-3-pro-preview", "gemini-3-flash-preview"),
    )
    def test_to_harness_config_for_requested_gemini_models(self, model_name: str):
        """Should parse requested Gemini model variants."""
        entry = MatrixEntry(
            harness="gemini",
            model=f"google/{model_name}",
        )
        config = entry.to_harness_config()
        assert config.agent.value == "gemini"
        assert config.model.provider == "google"
        assert config.model.name == model_name


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_generates_all_combinations(self):
        """Should generate all combinations."""
        config = MatrixConfig(
            runs=[
                {"harness": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"harness": "claude-code", "model": "anthropic/claude-sonnet-4-5"},
            ],
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2

    def test_generates_correct_combinations(self):
        """Should generate correct harness/model/rules combinations."""
        config = MatrixConfig(
            runs=[
                {"harness": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"harness": "codex-cli", "model": "codex/gpt-5.1"},
            ],
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
            task_path="task.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 3
