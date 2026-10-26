"""An empty ``--set key=`` override unsets a mapping key; a list item cannot be unset."""

from __future__ import annotations

import pytest

from raidar.design.experiment.loader import CliOverrides, load_experiment
from raidar.design.experiment.validation import SpecValidationError
from tests.experiment_spec_support import SMOKE_EXPERIMENT


def test_empty_reasoning_override_unsets_it_so_the_release_demands_one() -> None:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(SMOKE_EXPERIMENT, CliOverrides(assignments=("agents.0.reasoning=",)))
    assert [issue.loc for issue in caught.value.issues] == ["agents.0.reasoning"]
    assert "needs one of" in caught.value.issues[0].message
    assert (
        load_experiment(SMOKE_EXPERIMENT, CliOverrides(assignments=("agents.0.reasoning=low",)))
        .spec.agents[0]
        .reasoning
        == "low"
    )


def test_unsetting_a_list_item_is_refused() -> None:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(SMOKE_EXPERIMENT, CliOverrides(assignments=("arms.0=",)))
    assert "only a mapping key can be unset" in str(caught.value.issues)
