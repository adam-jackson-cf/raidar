"""Experiment schema rules applied to the repository's committed experiments."""

from __future__ import annotations

import pytest

from raidar.design.experiment.loader import load_experiment
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
