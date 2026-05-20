"""Tests for matrix generation."""

import pytest
from pydantic import ValidationError

from raidar.matrix import (
    AgentSpecInput,
    ExperimentConfig,
    MatrixAgentSpec,
    MatrixConfig,
    MatrixEntryInput,
    matrix_entry_agent_spec,
    resolve_matrix_jobs,
)


class TestMatrixAgentSpec:
    """Test MatrixAgentSpec functionality."""

    def test_workspace_suffix_generation(self):
        """Workspace suffix should be safe for filesystem."""
        entry = MatrixAgentSpec(
            harness="codex-cli",
            provider="openai",
            model="gpt-4o",
        )
        suffix = entry.workspace_suffix
        assert "/" not in suffix
        assert "codex-cli" in suffix
        assert "gpt-4o" in suffix

    def test_to_agent_spec(self):
        """Should convert to AgentSpec correctly."""
        entry = MatrixAgentSpec(
            harness="claude-code",
            provider="anthropic",
            model="claude-sonnet-4-5",
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
            provider="anthropic",
            model=model_name,
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
            provider="google",
            model=model_name,
        )
        config = entry.to_agent_spec()
        assert config.harness.value == "gemini"
        assert config.model.provider == "google"
        assert config.model.name == model_name


class TestGenerateMatrixEntries:
    """Test matrix entry generation."""

    def test_accepts_single_revision_benchmark_entries(self):
        """Should model multiple AgentSpecs against one scenario revision."""
        config = MatrixConfig(
            id="sample-benchmark",
            scenario="scenarios/sample",
            experiment=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            entries=[
                MatrixEntryInput(
                    id="codex-v001",
                    scenario_revision="v001",
                    agent=AgentSpecInput(
                        harness="codex-cli",
                        provider="openai",
                        model="gpt-5.4",
                        reasoning_effort="high",
                    ),
                ),
                MatrixEntryInput(
                    id="claude-v001",
                    scenario_revision="v001",
                    agent=AgentSpecInput(
                        harness="claude-code",
                        provider="anthropic",
                        model="claude-sonnet-4-5",
                    ),
                ),
            ],
        )

        assert [entry.scenario_revision for entry in config.entries] == ["v001", "v001"]
        assert matrix_entry_agent_spec(config.entries[0]).model == "gpt-5.4"

    def test_accepts_multi_revision_entries_for_one_agentspec(self):
        """Should model one AgentSpec across multiple scenario revisions."""
        config = MatrixConfig(
            id="sample-revision-comparison",
            scenario="scenarios/sample",
            experiment=ExperimentConfig(
                timeout_sec=300,
                repeats=3,
                repeat_parallel=1,
                retry_void=1,
            ),
            entries=[
                MatrixEntryInput(
                    id="codex-v001",
                    scenario_revision="v001",
                    agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
                ),
                MatrixEntryInput(
                    id="codex-v002",
                    scenario_revision="v002",
                    agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
                ),
            ],
        )

        assert [entry.scenario_revision for entry in config.entries] == ["v001", "v002"]

    def test_empty_config_generates_empty_list(self):
        """Empty config should raise validation error."""
        try:
            MatrixConfig(
                id="empty",
                scenario="scenarios/sample",
                experiment=ExperimentConfig(
                    timeout_sec=300,
                    repeats=3,
                    repeat_parallel=1,
                    retry_void=1,
                ),
                entries=[],
            )
        except ValidationError:
            assert True
        else:
            pytest.fail("MatrixConfig should require at least one run")

    def test_duplicate_entry_ids_rejected(self):
        """Matrix entry identifiers must be unique."""
        entry = MatrixEntryInput(
            id="duplicate",
            scenario_revision="v001",
            agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
        )
        with pytest.raises(ValidationError, match="duplicate entry ids"):
            MatrixConfig(
                id="duplicates",
                scenario="scenarios/sample",
                experiment=ExperimentConfig(
                    timeout_sec=300,
                    repeats=3,
                    repeat_parallel=1,
                    retry_void=1,
                ),
                entries=[entry, entry],
            )

    def test_old_agents_shape_is_rejected(self):
        """Config-level agents are no longer a valid matrix shape."""
        with pytest.raises(ValidationError):
            MatrixConfig.model_validate(
                {
                    "id": "legacy",
                    "scenario": "scenarios/sample",
                    "experiment": {
                        "timeout_sec": 300,
                        "repeats": 3,
                        "repeat_parallel": 1,
                        "retry_void": 1,
                    },
                    "agents": [{"harness": "codex-cli", "provider": "openai", "model": "gpt-5.4"}],
                }
            )


def _write_scenario(root, revision: str) -> None:
    revision_dir = root / revision
    revision_dir.mkdir(parents=True)
    (revision_dir / "scenario.yaml").write_text(
        "\n".join(
            [
                "name: sample",
                f"scenario_revision: {revision}",
                "parent_revision: null",
                "description: Sample scenario",
                "difficulty: easy",
                "category: smoke",
                "timeout_sec: 300",
                "starter:",
                "  root: starter",
                "verification:",
                "  min_quality_score: 0.0",
                "  gates: []",
                "  required_commands: []",
                "acceptance: {}",
                "scorers:",
                "  - id: resource-efficiency",
                "    version: 1",
                "    weight: 1.0",
                "prompt:",
                "  entry: prompt/task.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_resolve_matrix_jobs_loads_entry_scenarios(tmp_path):
    scenario_root = tmp_path / "scenarios" / "sample"
    _write_scenario(scenario_root, "v001")
    _write_scenario(scenario_root, "v002")
    config = MatrixConfig(
        id="sample",
        scenario=scenario_root,
        experiment=ExperimentConfig(timeout_sec=300, repeats=3, repeat_parallel=1, retry_void=1),
        entries=[
            MatrixEntryInput(
                id="codex-v001",
                scenario_revision="v001",
                agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
            ),
            MatrixEntryInput(
                id="codex-v002",
                scenario_revision="v002",
                agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
            ),
        ],
    )

    jobs = resolve_matrix_jobs(config, repo_root=tmp_path)

    assert [job.entry_id for job in jobs] == ["codex-v001", "codex-v002"]
    assert [job.scenario.scenario_revision for job in jobs] == ["v001", "v002"]


def test_resolve_matrix_jobs_rejects_missing_revision(tmp_path):
    scenario_root = tmp_path / "scenarios" / "sample"
    _write_scenario(scenario_root, "v001")
    config = MatrixConfig(
        id="sample",
        scenario=scenario_root,
        experiment=ExperimentConfig(timeout_sec=300, repeats=3, repeat_parallel=1, retry_void=1),
        entries=[
            MatrixEntryInput(
                id="codex-v002",
                scenario_revision="v002",
                agent=AgentSpecInput(harness="codex-cli", provider="openai", model="gpt-5.4"),
            )
        ],
    )

    with pytest.raises(FileNotFoundError, match="codex-v002"):
        resolve_matrix_jobs(config, repo_root=tmp_path)


def test_matrix_supports_multiple_entries():
    """Should handle larger stored matrices."""
    config = MatrixConfig(
        id="large",
        scenario="scenarios/sample",
        experiment=ExperimentConfig(
            timeout_sec=300,
            repeats=3,
            repeat_parallel=1,
            retry_void=1,
        ),
        entries=[
            MatrixEntryInput(
                id="codex-v001",
                scenario_revision="v001",
                agent=AgentSpecInput(
                    harness="codex-cli", provider="openai", model="gpt-5.4", reasoning_effort="high"
                ),
            ),
            MatrixEntryInput(
                id="claude-v001",
                scenario_revision="v001",
                agent=AgentSpecInput(
                    harness="claude-code", provider="anthropic", model="claude-sonnet-4-5"
                ),
            ),
            MatrixEntryInput(
                id="cursor-v001",
                scenario_revision="v001",
                agent=AgentSpecInput(harness="cursor", provider="openai", model="gpt-4o-mini"),
            ),
        ],
    )

    assert len(config.entries) == 3
