"""Tests for matrix generation."""

import pytest
from pydantic import ValidationError

from raidar.matrix import (
    AgentSpecInput,
    ExperimentConfig,
    MatrixAgentSpec,
    MatrixConfig,
    build_selected_matrix_config,
    generate_matrix_entries,
    matrix_selector_choices,
)


class TestMatrixAgentSpec:
    """Test MatrixAgentSpec functionality."""

    def test_workspace_suffix_generation(self):
        """Workspace suffix should be safe for filesystem."""
        entry = MatrixAgentSpec(
            harness="codex-cli",
            model="openai/gpt-4o",
        )
        suffix = entry.workspace_suffix
        assert "/" not in suffix
        assert "codex-cli" in suffix
        assert "gpt-4o" in suffix

    def test_to_agent_spec(self):
        """Should convert to AgentSpec correctly."""
        entry = MatrixAgentSpec(
            harness="claude-code",
            model="anthropic/claude-sonnet-4-5",
        )
        config = entry.to_agent_spec()
        assert config.harness.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == "claude-sonnet-4-5"

    @pytest.mark.parametrize(
        ("model_name"),
        ("claude-opus-4-6", "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-haiku-4-5"),
    )
    def test_to_agent_spec_for_requested_claude_models(self, model_name: str):
        """Should parse requested Claude model variants."""
        entry = MatrixAgentSpec(
            harness="claude-code",
            model=f"anthropic/{model_name}",
        )
        config = entry.to_agent_spec()
        assert config.harness.value == "claude-code"
        assert config.model.provider == "anthropic"
        assert config.model.name == model_name

    @pytest.mark.parametrize(
        ("model_name"),
        ("gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview"),
    )
    def test_to_agent_spec_for_requested_gemini_models(self, model_name: str):
        """Should parse requested Gemini model variants."""
        entry = MatrixAgentSpec(
            harness="gemini",
            model=f"google/{model_name}",
        )
        config = entry.to_agent_spec()
        assert config.harness.value == "gemini"
        assert config.model.provider == "google"
        assert config.model.name == model_name


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_generates_all_combinations(self):
        """Should generate all combinations."""
        config = MatrixConfig(
            experiment=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            agents=[
                AgentSpecInput(harness="codex-cli", model="codex/gpt-5.4-high"),
                AgentSpecInput(harness="claude-code", model="anthropic/claude-sonnet-4-5"),
            ],
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2

    def test_generates_correct_combinations(self):
        """Should generate correct AgentSpec combinations."""
        config = MatrixConfig(
            experiment=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            agents=[
                AgentSpecInput(harness="codex-cli", model="codex/gpt-5.4-high"),
                AgentSpecInput(harness="codex-cli", model="codex/gpt-5.1"),
            ],
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 2
        models = {e.model for e in entries}
        assert "codex/gpt-5.4-high" in models
        assert "codex/gpt-5.1" in models

    def test_empty_config_generates_empty_list(self):
        """Empty config should raise validation error."""
        try:
            MatrixConfig(
                experiment=ExperimentConfig(
                    timeout_sec=300,
                    repeats=3,
                    repeat_parallel=1,
                    retry_void=1,
                ),
                agents=[],
            )
        except ValidationError:
            assert True
        else:
            pytest.fail("MatrixConfig should require at least one run")

    def test_large_matrix_generation(self):
        """Should handle larger matrices."""
        config = MatrixConfig(
            experiment=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            agents=[
                AgentSpecInput(harness="codex-cli", model="codex/gpt-5.4-high"),
                AgentSpecInput(harness="claude-code", model="anthropic/claude-sonnet-4-5"),
                AgentSpecInput(harness="cursor", model="openai/gpt-4o-mini"),
            ],
        )
        entries = generate_matrix_entries(config)

        assert len(entries) == 3


def test_matrix_selector_choices_are_public_and_stable() -> None:
    assert matrix_selector_choices() == ("all", "codex", "gemini", "claude")


def test_build_selected_matrix_config_for_codex() -> None:
    config = build_selected_matrix_config(
        selector="codex",
        timeout_sec=1800,
        repeats=5,
        repeat_parallel=1,
        retry_void=0,
    )

    assert config.experiment.timeout_sec == 1800
    assert config.experiment.repeats == 5
    assert all(spec.harness == "codex-cli" for spec in config.agents)
    assert [spec.model for spec in config.agents] == [
        "codex/gpt-5.2-high",
        "codex/gpt-5.2-low",
        "codex/gpt-5.2-medium",
        "codex/gpt-5.3-codex-spark-high",
        "codex/gpt-5.3-codex-spark-low",
        "codex/gpt-5.3-codex-spark-medium",
        "codex/gpt-5.3-codex-spark-xhigh",
        "codex/gpt-5.4-extra-high",
        "codex/gpt-5.4-high",
        "codex/gpt-5.4-low",
        "codex/gpt-5.4-medium",
        "codex/gpt-5.4-mini",
        "codex/gpt-5.4-mini-low",
    ]


def test_build_selected_matrix_config_for_all() -> None:
    config = build_selected_matrix_config(
        selector="all",
        timeout_sec=1800,
        repeats=5,
        repeat_parallel=1,
        retry_void=0,
    )

    harnesses = [spec.harness for spec in config.agents]
    assert harnesses.count("codex-cli") == 13
    assert harnesses.count("gemini") == 3
    assert harnesses.count("claude-code") == 4
    assert len(config.agents) == 20
