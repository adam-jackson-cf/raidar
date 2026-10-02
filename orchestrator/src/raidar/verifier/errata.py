"""Errata-applied verdicts: the strict solve rule over the cases an errata keeps.

A problem version can carry a pinned errata (``raidar.problems.errata``): verifier
cases whose assertions its specification does not determine, each with a
disposition. Only ``exclude-from-solved`` cases change anything here: they
leave the errata-applied **solved** verdict. Nothing else changes. The raw
grade, its case statuses, the pass rates and Harbor's reward stay the SCB
grade, so the raw verdict is always available next to the errata-applied one.

The verifier receives the errata as ``ErrataRef`` (the lock's pinned digest and
the excluded case ids) and records an ``ErrataOutcome`` per step. Scorers
recompute the same verdict from the raw cases with ``errata_solved``.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from raidar.verifier.models import CaseResult, CaseStatus, VerifierGrade, VerifierModel


class ErrataRef(VerifierModel):
    """The errata a step is judged under: its pinned file digest and excluded case ids."""

    sha256: str = Field(min_length=1)
    exclude_from_solved: tuple[str, ...] = ()


class ErrataOutcome(VerifierModel):
    """One step's errata-applied verdict, recorded beside the raw grade.

    ``solved`` is ``None`` when the raw grade has no verdict (an infrastructure
    failure), never a pass or a fail.
    """

    sha256: str = Field(min_length=1)
    excluded_cases: tuple[str, ...]
    excluded_failing: tuple[str, ...]
    solved: bool | None


def _graded(cases: Iterable[CaseResult]) -> list[CaseResult]:
    return [case for case in cases if case.status != CaseStatus.SKIPPED]


def errata_solved(grade: VerifierGrade, excluded: frozenset[str]) -> bool | None:
    """The strict all-cases rule over the graded cases the errata keeps."""
    if grade.infra_failure is not None:
        return None
    kept = [case for case in _graded(grade.cases) if case.id not in excluded]
    return bool(kept) and all(case.status == CaseStatus.PASSED for case in kept)


def errata_outcome(grade: VerifierGrade, errata: ErrataRef) -> ErrataOutcome:
    excluded = frozenset(errata.exclude_from_solved)
    graded = [case for case in _graded(grade.cases) if case.id in excluded]
    return ErrataOutcome(
        sha256=errata.sha256,
        excluded_cases=tuple(sorted(case.id for case in graded)),
        excluded_failing=tuple(
            sorted(case.id for case in graded if case.status != CaseStatus.PASSED)
        ),
        solved=errata_solved(grade, excluded),
    )
