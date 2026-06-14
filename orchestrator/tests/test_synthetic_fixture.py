"""Behavior tests for synthetic benchmark fixture generation."""

import json

from raidar.synthetic import SYNTHETIC_MARKER, generate_synthetic_benchmark


def test_synthetic_experiments_are_labeled_and_benchmark_shaped(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)

    # 3 bugfix agent specs + skill scenario across 3 revisions x 2 specs.
    assert len(experiment_dirs) == 9
    revisions = {directory.name.split("__")[2] for directory in experiment_dirs}
    assert revisions == {"v001", "v002", "v003"}
    for experiment_dir in experiment_dirs:
        assert experiment_dir.name.startswith(f"{SYNTHETIC_MARKER}-")
        assert len(experiment_dir.name.split("__")) == 5
        summary = json.loads((experiment_dir / "experiment-summary.json").read_text("utf-8"))
        assert summary[SYNTHETIC_MARKER] is True
        assert summary["config"][SYNTHETIC_MARKER] is True
        assert isinstance(summary["findings"], list)
        assert summary["aggregate"]["run_count_total"] >= 3


def test_synthetic_fixture_covers_multi_spec_and_multi_revision(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)
    by_scenario_revision: dict[tuple[str, str], set[str]] = {}
    for directory in experiment_dirs:
        # name layout: synthetic-...__<scenario>__<revision>__<harness>__<model_label>
        parts = directory.name.split("__")
        key = (parts[1], parts[2])
        by_scenario_revision.setdefault(key, set()).add(f"{parts[3]} · {parts[4]}")

    # The bugfix revision is delivered by at least three distinct agent specs
    # (needed to exercise the comparison headline and Δ-vs-best framing).
    bugfix = by_scenario_revision[("bugfix-ledger-balance", "v001")]
    assert len(bugfix) >= 3

    # The skill scenario spans three revisions, each compared across >= 2 specs.
    skill_revisions = {
        rev for (scenario, rev) in by_scenario_revision if scenario == "skill-benchmark-coding-test"
    }
    assert skill_revisions == {"v001", "v002", "v003"}
    for rev in skill_revisions:
        assert len(by_scenario_revision[("skill-benchmark-coding-test", rev)]) >= 2


def test_synthetic_runs_persist_run_and_findings_artifacts(tmp_path):
    experiment_dirs = generate_synthetic_benchmark(tmp_path)
    # The volatile bugfix spec (gpt-5.5-low) carries the degraded run.
    low_dir = next(
        d for d in experiment_dirs if d.name.endswith("__gpt-5.5-low") and "bugfix" in d.name
    )

    run_dirs = sorted((low_dir / "runs").iterdir())
    assert len(run_dirs) == 4
    degraded = json.loads((run_dirs[-1] / "findings.json").read_text("utf-8"))
    categories = {finding["category"] for finding in degraded["findings"]}
    assert "failed-gate" in categories
    assert "missing-required-command" in categories
    assert "requirements-gap" in categories
    run_payload = json.loads((run_dirs[-1] / "run.json").read_text("utf-8"))
    assert run_payload["scores"]["metadata"][SYNTHETIC_MARKER] is True
