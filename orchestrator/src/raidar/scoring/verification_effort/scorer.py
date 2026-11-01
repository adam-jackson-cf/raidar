"""``verification-effort``: what the ``gate/1`` checks cost the agent, step by step.

Read deterministically from each step's host-written ``gate/1`` record
(``raidar.scoring.verification_effort.records``); no model is asked. Per step the scorer reports
the gate's seals, fix turns (their tokens, cost and time), first-pass and step-end cleanliness,
the blocking checks that failed and the fix turns' correctness; per trajectory, rates over decided
steps and totals over the gated steps. A trajectory without a ``gate/1`` record is
``not_applicable``.
"""

from __future__ import annotations

from pathlib import Path

from raidar.grading.models import VerifierGrade
from raidar.grading.pre_fix.store import read_pre_fix_grade
from raidar.grading.records import grading_identity
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.manifest import ScorerManifest
from raidar.scoring.framework.result import AssessResult
from raidar.scoring.verification_effort.assess import assess_trajectory
from raidar.scoring.verification_effort.grades import GradeReader
from raidar.scoring.verification_effort.manifest import VERIFICATION_EFFORT


class VerificationEffortScorer:
    """Deterministic scorer over each step's ``gate/1`` record."""

    def __init__(self, store_root: Path | None = None) -> None:
        self._store_root = store_root
        self._missing: list[str] = []

    @property
    def manifest(self) -> ScorerManifest:
        return VERIFICATION_EFFORT

    def awaiting_answers(self) -> bool:
        """True when a pre-fix snapshot was not graded yet (a later rescore completes it)."""
        return bool(self._missing)

    def _reader(self, ctx: AssessContext) -> GradeReader | None:
        root = self._store_root
        if root is None:
            return None
        records = {step.step: step.record for step in ctx.steps}

        def read(bundle: str, step: str, digest: str) -> VerifierGrade | None:
            record = records.get(step)
            if record is None:
                return None
            return read_pre_fix_grade(
                root, bundle, step, digest, identity=grading_identity(record.grader)
            )

        return read

    def assess(self, ctx: AssessContext) -> AssessResult:
        result, self._missing = assess_trajectory(ctx, self._reader(ctx))
        return result


def verification_effort_scorer() -> VerificationEffortScorer:
    """Registry factory without a store (pre-fix grades unavailable)."""
    return VerificationEffortScorer()


def verification_effort_scorer_for_store(store_root: Path) -> VerificationEffortScorer:
    """Store-bound factory: reads pre-fix grades from the Assessment store."""
    return VerificationEffortScorer(store_root)
