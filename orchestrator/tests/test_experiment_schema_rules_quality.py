"""Experiment schema rules applied to the repository's committed experiments."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from raidar.cli import main
from raidar.design.experiment.loader import load_experiment
from raidar.design.problem.store import ROOT_ENV
from tests.experiment_spec_support import (
    AGENTS_BENCHMARK,
    EXPERIMENTS,
    MECHANICS_SMOKE,
    SMOKE_EXPERIMENT,
)

pytestmark = pytest.mark.quality


def test_every_committed_experiment_is_in_its_kind_directory() -> None:
    files = sorted(EXPERIMENTS.glob("*/*/experiment.yaml"))
    assert {SMOKE_EXPERIMENT, AGENTS_BENCHMARK, MECHANICS_SMOKE} <= set(files)
    for path in files:
        spec = load_experiment(path).spec
        assert (path.parents[1].name, path.parent.name) == (spec.kind, spec.id)
        assert spec.id.split("__")[0] == spec.runtime == "python"


@pytest.mark.parametrize("committed", [MECHANICS_SMOKE, SMOKE_EXPERIMENT, AGENTS_BENCHMARK])
def test_validate_command_accepts_committed_experiments(committed: Path) -> None:
    result = CliRunner().invoke(main, ["validate", str(committed)])
    scorers = len(load_experiment(committed).spec.assessment.scorers)

    assert result.exit_code == 0, result.output
    assert "Experiment validation passed." in result.output
    assert f"scorer references: {scorers} (resolver available)" in result.output


def test_lock_command_locks_a_committed_experiment_at_its_keyed_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROOT_ENV, str(SMOKE_EXPERIMENT.parents[3] / "problems"))
    experiment = tmp_path / "oneshot.experiment.yaml"
    experiment.write_text(SMOKE_EXPERIMENT.read_text())

    result = CliRunner().invoke(main, ["lock", str(experiment)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "oneshot.experiment.lock.json").is_file()
    assert not (tmp_path / "experiment.lock.json").exists()
