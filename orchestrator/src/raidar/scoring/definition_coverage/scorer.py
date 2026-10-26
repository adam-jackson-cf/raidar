"""``definition-coverage``: per required unit, do the criteria citing it capture what it requires?

Stage A of FXL's process audit over a checkpoint's own define-stage records: the numbered issue
units (``units.json``, the required ones) and the sealed ``feature-definition.md``. A criterion
cites a unit when one of its ``Source:`` lines is the unit's text. Per required unit one Jev request
scores every citing criterion on three levels; the unit's reading is the best expected level among
them. A unit nothing cites is counted and not asked; a question Jev leaves undecided after the
retries is recorded as undecided. Nothing is turned into a pass or fail.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from raidar.scoring.coverage_inputs.asking import Asked, Asker, expected_level
from raidar.scoring.coverage_inputs.records import (
    Criterion,
    Defined,
    NotDefined,
    Unit,
    read_define,
)
from raidar.scoring.coverage_inputs.scorer import CoverageScorer, JevFactory, StepReading
from raidar.scoring.definition_coverage import questions
from raidar.scoring.definition_coverage.manifest import DEFINITION_COVERAGE
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import AssessStatus

_TOP = 2.0


def _entry(criterion: Criterion, result: Asked) -> dict[str, Any]:
    name = questions.question_id(criterion)
    levels = result.levels.get(name)
    return {
        "criterion": criterion.id,
        "question": name,
        "levels": list(levels) if levels else None,
        "expected": min(_TOP, expected_level(levels)) if levels else None,
        "undecided": result.undecided.get(name),
    }


class DefinitionCoverageScorer(CoverageScorer):
    question_set = questions.QUESTION_SET

    @property
    def manifest(self) -> ScorerManifest:
        return DEFINITION_COVERAGE

    def read(self, inputs: StepInputs, asker: Asker) -> StepReading:
        if inputs.trace_dir is None:
            return StepReading(AssessStatus.INSUFFICIENT_EVIDENCE, "the step kept no agent logs")
        found = read_define(inputs.trace_dir)
        if isinstance(found, NotDefined):
            return StepReading(AssessStatus.NOT_APPLICABLE, found.reason)
        if not isinstance(found, Defined):
            return StepReading(AssessStatus.INSUFFICIENT_EVIDENCE, found.reason)
        if found.criteria is None:
            return StepReading(
                AssessStatus.INSUFFICIENT_EVIDENCE,
                f"no sealed feature definition (define ended {found.ending})",
            )
        required = [unit for unit in found.units if unit.required]
        if not required:
            return StepReading(AssessStatus.NOT_APPLICABLE, "the issue has no required unit")
        return _read_units(required, found.criteria, found.ending, asker)


def _unit_row(
    unit: Unit, criteria: tuple[Criterion, ...], asker: Asker
) -> tuple[dict[str, Any], Counter[str], str | None]:
    """One unit's reading row, its counts and the resolved Jev model."""
    citing = [item for item in criteria if item.cites(unit)]
    row: dict[str, Any] = {"id": unit.id, "text": unit.text, "cited_by": [], "best_expected": None}
    count: Counter[str] = Counter()
    if not citing:
        count["uncited"] = 1
        return row, count, None
    result = asker.ask(*questions.request(unit, citing))
    row["cited_by"] = [_entry(item, result) for item in citing]
    row["calls"] = list(result.calls)
    best = [entry["expected"] for entry in row["cited_by"] if entry["expected"] is not None]
    row["best_expected"] = max(best) if best else None
    count["calls"] = len(result.calls)
    count["undecided"] = len(result.undecided)
    count["unjudged"] = 0 if best else 1
    return row, count, result.resolved_model


def _read_units(
    required: list[Unit], criteria: tuple[Criterion, ...], ending: str | None, asker: Asker
) -> StepReading:
    rows: list[dict[str, Any]] = []
    total: Counter[str] = Counter()
    model: str | None = None
    for unit in required:
        row, count, found = _unit_row(unit, criteria, asker)
        rows.append(row)
        total.update(count)
        model = found or model
    scored = [row["best_expected"] for row in rows if row["best_expected"] is not None]
    values = {
        "required_units": float(len(required)),
        "uncited_units": float(total["uncited"]),
        "judged_units": float(len(scored)),
        "undecided_units": float(total["unjudged"]),
        "undecided_questions": float(total["undecided"]),
        "jev_calls": float(total["calls"]),
    }
    gaps: dict[str, str] = {}
    if scored:
        values["mean_best_expected_level"] = sum(scored) / len(scored)
    else:
        gaps["mean_best_expected_level"] = "no required unit has a decided criterion score"
    return StepReading(
        AssessStatus.OK,
        values=values,
        gaps=gaps,
        record={"units": rows, "definition_ending": ending},
        undecided=total["undecided"],
        resolved_model=model,
    )


def definition_coverage_scorer(jev: JevFactory | None = None) -> DefinitionCoverageScorer:
    """Registry factory for ``definition-coverage``."""
    return DefinitionCoverageScorer(jev)
