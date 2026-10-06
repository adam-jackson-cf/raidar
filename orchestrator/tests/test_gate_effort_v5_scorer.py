"""gate-effort@5: gate-effort@4 whose evidence summaries count ``TEST.*`` in the tests family.

@4's observations already used the v4 family map; only its one-line gate-run
summaries in the evidence still said ``other`` for a tests-check finding.
@5 changes that text and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from raidar.scorers.catalog import default_scorer_registry
from raidar.scorers.gate_effort_v4 import GATE_EFFORT_V4, gate_effort_v4_scorer_for_store
from raidar.scorers.gate_effort_v5 import (
    GATE_EFFORT_V5,
    gate_effort_v5_scorer,
    gate_effort_v5_scorer_for_store,
)
from raidar.scorers.sdk.context import AssessContext
from raidar.scorers.sdk.result import AssessStatus
from raidar.scorers.sdk.validation import validate_result
from tests.test_gate_effort_scorer import _values
from tests.test_gate_effort_v4_scorer import CP1, _enforced, _store_grade


def test_v5_keeps_every_v4_observation_and_names_the_tests_family_in_its_evidence(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    _store_grade(store, CP1, passed=4, failed=0)
    steps = _enforced(tmp_path)
    ctx = AssessContext(problem="scb/forge", steps=steps, config={})
    v4 = validate_result(GATE_EFFORT_V4, ctx, gate_effort_v4_scorer_for_store(store).assess(ctx))
    v5 = validate_result(GATE_EFFORT_V5, ctx, gate_effort_v5_scorer_for_store(store).assess(ctx))

    assert _values(v5) == _values(v4)
    assert (v5.status, v5.reason) == (v4.status, v4.reason)
    old = [item.message for item in v4.evidence]
    new = [item.message for item in v5.evidence]
    assert old[0].endswith("refused, 2 blocking (complexity 1, other 1)")
    assert new[0].endswith("refused, 2 blocking (complexity 1, tests 1)")
    assert [item.model_copy(update={"message": ""}) for item in v5.evidence] == [
        item.model_copy(update={"message": ""}) for item in v4.evidence
    ]


def test_v5_is_registered_store_bound_with_v4s_metrics_and_refuses_config() -> None:
    registration = default_scorer_registry().get("gate-effort@5")

    assert registration.store_implementation is not None
    assert registration.manifest.metrics == GATE_EFFORT_V4.metrics
    assert registration.manifest.version == "5"
    ctx = AssessContext(problem="scb/forge", steps=(), config={"x": 1})
    result = gate_effort_v5_scorer().assess(ctx)
    assert (result.status, result.reason) == (AssessStatus.ERROR, "gate-effort@5 takes no config")
