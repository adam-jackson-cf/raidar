"""Small fail-closed edge cases for shared owner boundaries."""

from types import SimpleNamespace

import pytest

from raidar.runtime import wait
from raidar.runtime.identifiers import slug_fragment
from raidar.scorers.design_to_code import _visual_region_scores
from raidar.scorers.paths import validate_scorer_relative_path
from raidar.starter.catalog import resolve_starter_source
from raidar.storage import _failed_categories


def test_named_wait_helpers_use_owned_retry_delays(monkeypatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr(wait.time, "sleep", delays.append)

    wait.wait_for_cache_lock_retry()
    wait.wait_for_harbor_rate_limit_retry()

    assert delays == [0.1, wait.HARBOR_RATE_LIMIT_RETRY_DELAY_SEC]


def test_shared_paths_and_identifiers_reject_invalid_inputs(tmp_path) -> None:
    with pytest.raises(ValueError, match="invalid identifier"):
        slug_fragment("---")
    with pytest.raises(ValueError, match="parent traversal"):
        validate_scorer_relative_path("../escape", field_name="evidence.path")
    with pytest.raises(FileNotFoundError, match="Starter root not found"):
        resolve_starter_source(
            tmp_path,
            "missing",
            scenario_name="demo",
            scenario_revision="v001",
        )


def test_malformed_optional_analysis_inputs_are_ignored() -> None:
    run = SimpleNamespace(scores=SimpleNamespace(metadata={"process": []}))

    assert _failed_categories(run) == {}
    assert _visual_region_scores([{"comparison": "invalid"}], 0.8) == []
