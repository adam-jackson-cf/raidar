"""Behavior tests for synthetic benchmark fixture generation."""

import json

from raidar.synthetic import SYNTHETIC_MARKER, generate_synthetic_benchmark


def test_synthetic_experiments_are_labeled_and_benchmark_shaped(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)

    assert len(experiment_dirs) == 6
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


def test_synthetic_visual_experiments_carry_screenshot_evidence(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)
    visual_dirs = [d for d in experiment_dirs if "homepage-hero-replication" in d.name]

    assert len(visual_dirs) == 2
    harnesses = {directory.name.split("__")[3] for directory in visual_dirs}
    assert harnesses == {"claude-code", "codex-cli"}
    for experiment_dir in visual_dirs:
        summary = json.loads((experiment_dir / "experiment-summary.json").read_text("utf-8"))
        assert summary["sample"]["scenario_family"] == "visual-ui-implementation"
        for run_dir in (experiment_dir / "runs").iterdir():
            run_payload = json.loads((run_dir / "run.json").read_text("utf-8"))
            visual = run_payload["scores"]["visual"]
            assert visual["capture_succeeded"] is True
            assert visual["region_evidence_status"] == "present"
            for asset in ("reference.png", "actual.png", "diff.png"):
                assert (run_dir / "visual" / asset).is_file()
            for region in visual["regional_scores"]:
                assert (run_dir / region["diff_path"]).is_file()


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
