"""Tests for matrix generation."""

import pytest
from pydantic import ValidationError

from raidar.matrix import (
    ExperimentConfig,
    MatrixConfig,
    MatrixEntry,
    generate_matrix_entries,
)


class TestMatrixEntry:
    """Test MatrixEntry functionality."""

    def test_workspace_suffix_generation(self):
        """Workspace suffix should be safe for filesystem."""
        entry = MatrixEntry(
            agent="codex-cli",
            model="openai/gpt-4o",
        )
        suffix = entry.workspace_suffix
        assert "/" not in suffix
        assert "codex-cli" in suffix
        assert "gpt-4o" in suffix

    def test_to_agent_config(self):
        """Should convert to AgentRunConfig correctly."""
        entry = MatrixEntry(
            agent="claude-code",
            model="anthropic/claude-sonnet-4-5",
        )
        config = entry.to_agent_config()
        assert config.agent.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == "claude-sonnet-4-5"

    @pytest.mark.parametrize(
        ("model_name"),
        ("claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"),
    )
    def test_to_harness_config_for_requested_claude_models(self, model_name: str):
        """Should parse requested Claude model variants."""
        entry = MatrixEntry(
            agent="claude-code",
            model=f"anthropic/{model_name}",
        )
        config = entry.to_agent_config()
        assert config.agent.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == model_name

    @pytest.mark.parametrize(
        ("model_name"),
        ("gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview"),
    )
    def test_to_harness_config_for_requested_gemini_models(self, model_name: str):
        """Should parse requested Gemini model variants."""
        entry = MatrixEntry(
            agent="gemini",
            model=f"google/{model_name}",
        )
        config = entry.to_agent_config()
        assert config.agent.value == "gemini"
        assert config.model.provider == "google"
        assert config.model.name == model_name


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_generates_all_combinations(self):
        """Should generate all combinations."""
        config = MatrixConfig(
            suite=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            runs=[
                {"agent": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"agent": "claude-code", "model": "anthropic/claude-sonnet-4-5"},
            ],
            scenario_path="scenario.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2

    def test_generates_correct_combinations(self):
        """Should generate correct agent/model combinations."""
        config = MatrixConfig(
            suite=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            runs=[
                {"agent": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"agent": "codex-cli", "model": "codex/gpt-5.1"},
            ],
            scenario_path="scenario.yaml",
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
                suite=ExperimentConfig(
                    timeout_sec=300,
                    repeats=3,
                    repeat_parallel=1,
                    retry_void=1,
                ),
                runs=[],
                scenario_path="scenario.yaml",
            )
        except ValidationError:
            assert True
        else:
            pytest.fail("MatrixConfig should require at least one run")

    def test_large_matrix_generation(self):
        """Should handle larger matrices."""
        config = MatrixConfig(
            suite=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            runs=[
                {"agent": "codex-cli", "model": "codex/gpt-5.2-high"},
                {"agent": "claude-code", "model": "anthropic/claude-sonnet-4-5"},
                {"agent": "cursor", "model": "openai/gpt-4o-mini"},
            ],
            scenario_path="scenario.yaml",
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 3
