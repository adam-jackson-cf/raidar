"""An empty ``--set key=`` override unsets a mapping key; a list item cannot be unset."""

from __future__ import annotations

import pytest

from raidar.design.experiment.loader import CliOverrides, load_experiment
from raidar.design.experiment.validation import SpecValidationError
from tests.experiment_spec_support import SMOKE_EXPERIMENT


def test_empty_reasoning_effort_override_unsets_it_so_haiku_validates() -> None:
    claude_haiku = (
        "agents.0.name=claude-code",
        "agents.0.provider=anthropic",
        "agents.0.model=claude-haiku-4-5",
        "agents.0.version='2.1.285'",
    )
    unset = load_experiment(
        SMOKE_EXPERIMENT, CliOverrides(assignments=(*claude_haiku, "agents.0.reasoning_effort="))
    )
    assert unset.spec.agents[0].reasoning_effort is None
    assert unset.spec.agents[0].model == "claude-haiku-4-5"
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(
            SMOKE_EXPERIMENT,
            CliOverrides(assignments=(*claude_haiku, "agents.0.reasoning_effort=low")),
        )
    assert any(issue.loc.endswith("reasoning_effort") for issue in caught.value.issues)


def test_unsetting_a_list_item_is_refused() -> None:
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(SMOKE_EXPERIMENT, CliOverrides(assignments=("arms.0=",)))
    assert "only a mapping key can be unset" in str(caught.value.issues)
