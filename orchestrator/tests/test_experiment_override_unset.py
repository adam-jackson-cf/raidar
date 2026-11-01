"""An empty ``--set key=`` override unsets a mapping key; a list item cannot be unset."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.design.experiment.loader import CliOverrides, load_experiment
from raidar.design.experiment.validation import SpecValidationError
from tests.experiment_spec_support import experiment_document, write_experiment


def test_empty_reasoning_override_unsets_it_so_the_release_demands_one(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path, CliOverrides(assignments=("agents.0.reasoning=",)))

    assert [issue.loc for issue in caught.value.issues] == ["agents.0.reasoning"]
    reset = load_experiment(path, CliOverrides(assignments=("agents.0.reasoning=low",)))
    assert reset.spec.agents[0].reasoning == "low"


def test_unsetting_a_list_item_is_refused(tmp_path: Path) -> None:
    path = write_experiment(tmp_path, experiment_document())

    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path, CliOverrides(assignments=("arms.0=",)))

    assert "only a mapping key can be unset" in str(caught.value.issues)
