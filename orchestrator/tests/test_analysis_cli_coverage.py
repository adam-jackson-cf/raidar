"""The analysis command retains registered evidence and labels an unmodified locked decision."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

import raidar.cli.analyze as analyze_cli
from raidar.evaluation.analysis.outcomes import PoolFinding, PoolingReport
from tests.analysis_support import analysis_lock, inputs, paired_arms


def test_locked_defaults_preserve_pins_without_claiming_thresholds_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = analysis_lock(
        ["correctness@10"],
        min_pairs=5,
        max_exclusion_fraction=0.2,
        alpha=0.05,
        min_effects={},
    )
    loaded = replace(
        inputs(paired_arms("solve_rate", [0.0, 0.0], [1.0, 1.0]), 2),
        primary_outcome="solve_rate",
        errata={"toy@1": {"sha256": "a" * 64, "exclude_from_solved": []}},
        solvable={"toy@1": {"sha256": "b" * 64, "cases": {"checkpoint_1": 1}}},
        pooling=PoolingReport(findings=(PoolFinding(field="subject", message="different arms"),)),
    )
    job, store = tmp_path / "job", tmp_path / "store"
    job.mkdir()
    store.mkdir()
    monkeypatch.setattr(analyze_cli, "load_inputs", lambda request: (loaded, lock))
    output = tmp_path / "output"
    result = CliRunner().invoke(
        analyze_cli.analyze,
        [str(job), "--store", str(store), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((output / "analysis.json").read_text())
    assert report["errata"] == dict(loaded.errata)
    assert report["solvable"] == dict(loaded.solvable)
    assert report["settings"]["pinned_by_lock"]
    assert report["settings"]["changed_from_defaults"] == []
    assert "settings changed from defaults" not in result.output
    assert "records integrity unmeasured" not in result.output
    assert "pooling override subject" in result.output
    assert "raw solve_rate" not in result.output
