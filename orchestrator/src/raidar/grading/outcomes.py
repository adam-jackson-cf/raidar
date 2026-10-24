"""Pure result types of grading a snapshot: one run's outcome and a whole step's grade.

Kept free of sandbox/docker code so scorers and records can import the types
without pulling in grading mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from raidar.grading.models import (
    CaseResult,
    InfraFailure,
    ParityVerdict,
    RunEvidence,
    VerifierGrade,
)
from raidar.grading.submission_deps import SubmissionDependencies


@dataclass(frozen=True)
class RunOutcome:
    role: str
    cases: tuple[CaseResult, ...]
    infra_failure: InfraFailure | None
    evidence: RunEvidence | None
    timing_sensitive: tuple[str, ...]
    seconds: float
    dependencies: SubmissionDependencies | None = None


@dataclass(frozen=True)
class StepGrade:
    grade: VerifierGrade
    grading: RunOutcome
    coverage: RunOutcome | None
    parity: ParityVerdict | None
    parity_differences: tuple[str, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)
