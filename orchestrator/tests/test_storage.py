"""Tests for run storage and aggregation."""

from datetime import UTC, datetime
from pathlib import Path

from raidar.schemas.scorecard import EvalConfig, EvalRun, Scorecard
from raidar.storage import (
    aggregate_results,
    export_to_csv,
    load_all_runs,
    load_run,
    save_run,
)


class TestSaveAndLoadRun:
    """Test run persistence."""

    def test_save_run_creates_file(self, sample_eval_run: EvalRun, tmp_results_dir: Path):
        """Should create JSON file when saving run."""
        path = save_run(sample_eval_run, tmp_results_dir)

        assert path.exists()
        assert path.suffix == ".json"

    def test_load_run_returns_same_data(self, sample_eval_run: EvalRun, tmp_results_dir: Path):
        """Should load same data that was saved."""
        path = save_run(sample_eval_run, tmp_results_dir)
        loaded = load_run(path)

        assert loaded.id == sample_eval_run.id
        assert loaded.config.agent == sample_eval_run.config.agent
        assert loaded.config.model == sample_eval_run.config.model

    def test_save_creates_directory_if_missing(self, sample_eval_run: EvalRun, tmp_path: Path):
        """Should create directory if it doesn't exist."""
        results_dir = tmp_path / "new" / "nested" / "dir"
        assert not results_dir.exists()

        save_run(sample_eval_run, results_dir)

        assert results_dir.exists()


class TestLoadAllRuns:
    """Test loading multiple runs."""

    def test_loads_all_runs(self, sample_eval_run: EvalRun, tmp_results_dir: Path):
        """Should load all runs from directory."""
        # Save multiple runs
        for i in range(3):
            run = sample_eval_run.model_copy()
            run.id = f"run-{i:03d}"
            save_run(run, tmp_results_dir)

        runs = load_all_runs(tmp_results_dir)

        assert len(runs) == 3

    def test_returns_empty_list_for_empty_dir(self, tmp_results_dir: Path):
        """Should return empty list for empty directory."""
        runs = load_all_runs(tmp_results_dir)

        assert runs == []

    def test_skips_invalid_files(self, sample_eval_run: EvalRun, tmp_results_dir: Path):
        """Should skip invalid JSON files."""
        # Save valid run
        save_run(sample_eval_run, tmp_results_dir)

        # Create invalid file
        (tmp_results_dir / "invalid.json").write_text("not valid json{")

        runs = load_all_runs(tmp_results_dir)

        assert len(runs) == 1


class TestAggregateResults:
    """Test result aggregation."""

    def test_returns_empty_for_no_runs(self):
        """Should return minimal stats for no runs."""
        result = aggregate_results([])

        assert result["total_runs"] == 0

    def test_aggregates_by_agent(self, sample_eval_run: EvalRun):
        """Should aggregate results by agent."""
        runs = [sample_eval_run]
        result = aggregate_results(runs)

        assert "by_agent" in result
        assert sample_eval_run.config.agent in result["by_agent"]

    def test_aggregates_by_model(self, sample_eval_run: EvalRun):
        """Should aggregate results by model."""
        runs = [sample_eval_run]
        result = aggregate_results(runs)

        assert "by_model" in result
        assert sample_eval_run.config.model in result["by_model"]

    def test_aggregates_stability_by_config(self, sample_eval_run: EvalRun):
        """Should expose validity and variance by config key."""
        runs = [sample_eval_run]
        result = aggregate_results(runs)

        assert "by_config" in result
        assert len(result["by_config"]) == 1
        stats = next(iter(result["by_config"].values()))
        assert stats["validity_rate"] == 1.0
        assert stats["performance_pass_rate"] == 1.0
        assert stats["score_variance"] == 0.0

    def test_calculates_average_score(self):
        """Should calculate average scores correctly."""
        run1 = EvalRun(
            id="run-001",
            timestamp=datetime.now(UTC).isoformat(),
            config=EvalConfig(
                model="openai/gpt-4o",
                agent="codex-cli",
                scenario_name="test",
                scenario_revision="v001",
                starter_root="starter",
                evaluation_profile=(
                    "v2:functional+acceptance+verification-stability+"
                    "execution-validity+resource-efficiency"
                ),
            ),
            duration_sec=60,
            scores=Scorecard(),
        )
        run2 = EvalRun(
            id="run-002",
            timestamp=datetime.now(UTC).isoformat(),
            config=EvalConfig(
                model="openai/gpt-4o",
                agent="codex-cli",
                scenario_name="test",
                scenario_revision="v001",
                starter_root="starter",
                evaluation_profile=(
                    "v2:functional+acceptance+verification-stability+"
                    "execution-validity+resource-efficiency"
                ),
            ),
            duration_sec=60,
            scores=Scorecard(),
        )

        result = aggregate_results([run1, run2])

        # Both have default scores, so average should be equal to one
        assert result["by_agent"]["codex-cli"]["count"] == 2
        assert isinstance(result["by_agent"]["codex-cli"]["avg_score"], float)

    def test_unscored_runs_excluded_from_scored_aggregates(self, sample_eval_run: EvalRun):
        """Unscored runs should not affect scored validity-rate/average."""
        valid = sample_eval_run.model_copy(deep=True)
        valid.id = "valid-run"
        valid.scores.unscored = False
        valid.scores.execution_validity.checks = []
        valid.scores.performance_gates.checks = []

        unscored_run = sample_eval_run.model_copy(deep=True)
        unscored_run.id = "unscored-run"
        unscored_run.scores.unscored = True
        unscored_run.scores.unscored_reasons = ["provider_rate_limit"]
        unscored_run.scores.execution_validity.checks = []
        unscored_run.scores.performance_gates.checks = []

        result = aggregate_results([valid, unscored_run])
        stats = result["by_agent"][valid.config.agent]
        assert stats["count"] == 2
        assert stats["scored_count"] == 1
        assert stats["unscored_count"] == 1
        assert stats["validity_rate"] == 1.0
        assert stats["performance_pass_rate"] == 1.0


class TestExportCsv:
    """Test CSV export fields."""

    def test_export_to_csv_includes_evaluation_profile_and_metrics(
        self, sample_eval_run: EvalRun, tmp_path: Path
    ):
        sample_eval_run.scores.metric_results = []
        sample_eval_run.scores.metadata["harbor"] = {
            "phase_timings_sec": {},
            "agent_overhead_sec": 1.23,
        }
        output = tmp_path / "runs.csv"
        export_to_csv([sample_eval_run], output)
        payload = output.read_text(encoding="utf-8")
        assert "evaluation_profile" in payload
        assert "metric_results" in payload
        assert "agent_overhead_sec" in payload
        assert "1.23" in payload
        assert sample_eval_run.config.evaluation_profile in payload
