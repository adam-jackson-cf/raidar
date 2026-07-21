"""Contract tests for scorer-owned metric collection."""

from pathlib import Path

import pytest

from raidar.runtime.scoring_outputs import collect_metric_scores_from_scorers
from raidar.schemas.scenario import ScenarioScorerRef
from raidar.scorers.base import ScorerContext, ScorerEvidence
from raidar.synthetic import _BUGFIX_SCENARIO


class StaticEvidenceAdapter:
    def __init__(self, outputs: dict[str, tuple[dict[str, object], ...]]) -> None:
        self.outputs = outputs

    def collect_evidence(
        self,
        scorer_ref: ScenarioScorerRef,
        context: ScorerContext,
    ) -> ScorerEvidence:
        del context
        return ScorerEvidence(metric_scores=self.outputs.get(scorer_ref.id, ()))


def _context() -> ScorerContext:
    return ScorerContext(
        workspace=Path("."),
        scenario_dir=Path("."),
        scenario=_BUGFIX_SCENARIO,
        execution=None,
        resource_efficiency=None,
        execution_validity=None,
    )


def _metric(metric_id: str) -> dict[str, object]:
    return {"metric_id": metric_id, "score": 1.0, "passed": True}


def test_metric_collection_rejects_missing_resolved_outputs() -> None:
    with pytest.raises(ValueError, match="did not emit all resolved metrics"):
        collect_metric_scores_from_scorers(
            _BUGFIX_SCENARIO,
            _context(),
            StaticEvidenceAdapter({}),
        )


def test_metric_collection_rejects_unowned_outputs() -> None:
    adapter = StaticEvidenceAdapter({"bugfix": (_metric("resource-efficiency"),)})

    with pytest.raises(ValueError, match="emitted unowned metric"):
        collect_metric_scores_from_scorers(_BUGFIX_SCENARIO, _context(), adapter)


def test_metric_collection_rejects_duplicate_outputs() -> None:
    duplicate = _metric("defect-resolution")
    adapter = StaticEvidenceAdapter({"bugfix": (duplicate, duplicate)})

    with pytest.raises(ValueError, match="Duplicate scorer metric output"):
        collect_metric_scores_from_scorers(_BUGFIX_SCENARIO, _context(), adapter)
