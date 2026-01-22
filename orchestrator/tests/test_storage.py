"""Tests for run storage and aggregation."""

from datetime import UTC, datetime
from pathlib import Path

from agentic_eval.schemas.scorecard import EvalConfig, EvalRun, Scorecard
from agentic_eval.storage import (
    aggregate_results,
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
        assert loaded.config.harness == sample_eval_run.config.harness
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

    def test_aggregates_by_harness(self, sample_eval_run: EvalRun):
        """Should aggregate results by harness."""
        runs = [sample_eval_run]
        result = aggregate_results(runs)

        assert "by_harness" in result
        assert sample_eval_run.config.harness in result["by_harness"]

    def test_aggregates_by_model(self, sample_eval_run: EvalRun):
        """Should aggregate results by model."""
        runs = [sample_eval_run]
        result = aggregate_results(runs)

        assert "by_model" in result
        assert sample_eval_run.config.model in result["by_model"]

    def test_calculates_average_score(self):
        """Should calculate average scores correctly."""
        run1 = EvalRun(
            id="run-001",
            timestamp=datetime.now(UTC).isoformat(),
            config=EvalConfig(
                model="openai/gpt-4o",
                harness="codex",
                rules_variant="strict",
                task_name="test",
            ),
            duration_sec=60,
            scores=Scorecard(),
        )
        run2 = EvalRun(
            id="run-002",
            timestamp=datetime.now(UTC).isoformat(),
            config=EvalConfig(
                model="openai/gpt-4o",
                harness="codex",
                rules_variant="strict",
                task_name="test",
            ),
            duration_sec=60,
            scores=Scorecard(),
        )

        result = aggregate_results([run1, run2])

        # Both have default scores, so average should be equal to one
        assert result["by_harness"]["codex"]["count"] == 2
        assert isinstance(result["by_harness"]["codex"]["avg_score"], float)
