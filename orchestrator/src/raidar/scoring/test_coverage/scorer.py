"""``test-coverage``: per criterion, do the builder's final mapped tests exercise it?

Stage B of FXL's process audit over a checkpoint's own records: the sealed definition's
criteria (regression ones included), the build's final ``ac_tests`` map and the final test
files kept beside it.
Per criterion, each mapped test is shown to Jev with one hop of helpers and scored on three levels
(``questions``). A mapped test the kept files do not hold is counted as unread and not asked; a
criterion with no mapped test is counted as unmapped. A test with no real assertion, or marked
skipped, is held to level 1 whatever Jev said, as FXL does; its Jev probabilities stay in the
record. A criterion's reading is the best expected level of its tests. Nothing is turned into a
pass or fail.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from raidar.scoring.coverage_inputs.asking import Asked, Asker, expected_level
from raidar.scoring.coverage_inputs.records import (
    Built,
    Criterion,
    Defined,
    NotDefined,
    read_build,
    read_define,
)
from raidar.scoring.coverage_inputs.scorer import CoverageScorer, JevFactory, StepReading
from raidar.scoring.coverage_inputs.testsource import ReadTest, read_test
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.test_coverage import questions
from raidar.scoring.test_coverage.manifest import TEST_COVERAGE

_TOP = 2.0
_WEAK = 1.0


class TestCoverageScorer(CoverageScorer):
    __test__ = False  # not a pytest class
    question_set = questions.QUESTION_SET

    @property
    def manifest(self) -> ScorerManifest:
        return TEST_COVERAGE

    def read(self, inputs: StepInputs, asker: Asker) -> StepReading:
        if inputs.trace_dir is None:
            return StepReading(AssessStatus.INSUFFICIENT_EVIDENCE, "the step kept no agent logs")
        defined = read_define(inputs.trace_dir)
        if isinstance(defined, NotDefined):
            return StepReading(AssessStatus.NOT_APPLICABLE, defined.reason)
        reason = (
            defined.reason
            if not isinstance(defined, Defined)
            else None
            if defined.criteria is not None
            else f"no sealed feature definition (define ended {defined.ending})"
        )
        if reason is not None or not isinstance(defined, Defined):
            return StepReading(AssessStatus.INSUFFICIENT_EVIDENCE, reason)
        built = read_build(inputs.trace_dir)
        if not isinstance(built, Built):
            return StepReading(AssessStatus.INSUFFICIENT_EVIDENCE, built.reason)
        criteria = list(defined.criteria or ())
        if not criteria:
            return StepReading(AssessStatus.NOT_APPLICABLE, "the definition has no criterion")
        return _read_criteria(criteria, built, asker)


def _held(test: ReadTest) -> bool:
    return test.has_assertion and not test.skipped


def _test_entry(test: ReadTest, name: str, result: Asked) -> dict[str, Any]:
    levels = result.levels.get(name)
    expected = expected_level(levels) if levels else None
    if expected is not None:
        expected = min(_TOP, expected if _held(test) else min(_WEAK, expected))
    return {
        "test": test.id,
        "question": name,
        "has_assertion": test.has_assertion,
        "skipped": test.skipped,
        "levels": list(levels) if levels else None,
        "expected": expected,
        "undecided": result.undecided.get(name),
    }


def _ask_criterion(
    criterion: Criterion, tests: list[ReadTest], asker: Asker
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    entries: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    model: str | None = None
    by_id = {test.id: test for test in tests}
    for state, asking in questions.requests(criterion, tests):
        result = asker.ask(state, asking)
        calls += result.calls
        model = result.resolved_model or model
        shown: dict[str, Any] = state["tests"]  # type: ignore[assignment]
        entries += [_test_entry(by_id[shown[name]["id"]], name, result) for name in asking]
    return entries, calls, model


def _mapped_tests(sources: Path, names: Sequence[str]) -> tuple[list[ReadTest], list[str]]:
    read = [read_test(sources, name) for name in names]
    unread = [name for name, test in zip(names, read, strict=True) if test is None]
    return [test for test in read if test is not None], unread


def _criterion_row(
    criterion: Criterion, built: Built, asker: Asker
) -> tuple[dict[str, Any], Counter[str], str | None]:
    """One criterion's reading row, its counts and the resolved Jev model."""
    names = built.ac_tests.get(criterion.id, ())
    row: dict[str, Any] = {
        "id": criterion.id,
        "text": criterion.text,
        "regression": criterion.regression,
        "mapped": list(names),
        "best_expected": None,
    }
    count: Counter[str] = Counter()
    if not names:
        count["unmapped"] = 1
        return row, count, None
    tests, row["unread"] = _mapped_tests(Path(built.sources), names)
    row["tests"], row["calls"], model = _ask_criterion(criterion, tests, asker)
    best = [entry["expected"] for entry in row["tests"] if entry["expected"] is not None]
    row["best_expected"] = max(best) if best else None
    count["unread"] = len(row["unread"])
    count["calls"] = len(row["calls"])
    count["undecided"] = len(row["tests"]) - len(best)
    count["unjudged"] = 0 if best or not tests else 1
    return row, count, model


def _read_criteria(criteria: list[Criterion], built: Built, asker: Asker) -> StepReading:
    rows: list[dict[str, Any]] = []
    total: Counter[str] = Counter()
    model: str | None = None
    for criterion in criteria:
        row, count, found = _criterion_row(criterion, built, asker)
        rows.append(row)
        total.update(count)
        model = found or model
    scored = [row["best_expected"] for row in rows if row["best_expected"] is not None]
    values = {
        "criteria": float(len(criteria)),
        "unmapped_criteria": float(total["unmapped"]),
        "judged_criteria": float(len(scored)),
        "undecided_criteria": float(total["unjudged"]),
        "unread_tests": float(total["unread"]),
        "undecided_questions": float(total["undecided"]),
        "jev_calls": float(total["calls"]),
    }
    gaps: dict[str, str] = {}
    if scored:
        values["mean_best_expected_level"] = sum(scored) / len(scored)
    else:
        gaps["mean_best_expected_level"] = "no criterion has a decided test score"
    return StepReading(
        AssessStatus.OK,
        values=values,
        gaps=gaps,
        record={"criteria": rows},
        undecided=total["undecided"],
        resolved_model=model,
    )


def make_test_coverage_scorer(jev: JevFactory | None = None) -> TestCoverageScorer:
    """Registry factory for ``test-coverage``."""
    return TestCoverageScorer(jev)
