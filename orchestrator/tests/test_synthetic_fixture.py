"""Behavior tests for synthetic benchmark fixture generation."""

import json

from raidar.synthetic import SYNTHETIC_MARKER, generate_synthetic_benchmark


def test_synthetic_experiments_are_labeled_and_benchmark_shaped(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)

    assert len(experiment_dirs) == 4
    revisions = {directory.name.split("__")[2] for directory in experiment_dirs}
    assert revisions == {"v001", "v003"}
    for experiment_dir in experiment_dirs:
        assert experiment_dir.name.startswith(f"{SYNTHETIC_MARKER}-")
        assert len(experiment_dir.name.split("__")) == 5
        summary = json.loads((experiment_dir / "experiment-summary.json").read_text("utf-8"))
        assert summary[SYNTHETIC_MARKER] is True
        assert summary["config"][SYNTHETIC_MARKER] is True
        assert isinstance(summary["findings"], list)
        assert summary["aggregate"]["run_count_total"] >= 3


def test_synthetic_runs_persist_run_and_findings_artifacts(tmp_path):
    low_dir = generate_synthetic_benchmark(tmp_path)[0]

    run_dirs = sorted((low_dir / "runs").iterdir())
    assert len(run_dirs) == 4
    degraded = json.loads((run_dirs[-1] / "findings.json").read_text("utf-8"))
    categories = {finding["category"] for finding in degraded["findings"]}
    assert "failed-gate" in categories
    assert "missing-required-command" in categories
    assert "requirements-gap" in categories
    run_payload = json.loads((run_dirs[-1] / "run.json").read_text("utf-8"))
    assert run_payload["scores"]["metadata"][SYNTHETIC_MARKER] is True
