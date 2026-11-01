"""The fix turns' correctness: the pre-fix snapshot's grade against the step's final grade.

``pre_fix_pass_rate`` is the case pass rate of the workspace the step's first fix turn started from
(the ``gate/1`` retained pre-fix tree, graded offline into the Assessment store by
``raidar.grading.pre_fix.grade``) and ``correctness_delta`` the step's final case pass rate (its
verifier record) minus it. A step without a fix turn is ``not_applicable``; an ungraded snapshot is
``unavailable`` (the Assessment is then provisional and a later rescore, after grading, supersedes
it).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.verification_effort.measured import Measured, same_for
from raidar.scoring.verification_effort.metrics import CORRECTNESS_DELTA, PRE_FIX_PASS_RATE
from raidar.scoring.verification_effort.records import StepGate

_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
NO_FIX_ROUND = "no fix turn in this step: no pre-fix snapshot to compare"
NAMES = (PRE_FIX_PASS_RATE, CORRECTNESS_DELTA)

GradeReader = Callable[[str, str, str], Any]


@dataclass
class Grades:
    """The pre-fix grade reader, and the snapshots it found no grade for."""

    reader: GradeReader | None
    missing: list[str] = field(default_factory=list)


def _first_digest(gate: StepGate) -> tuple[str | None, str | None]:
    """(digest of the first fix turn's starting tree, why there is none)."""
    if not gate.fixes:
        return None, NO_FIX_ROUND
    if not gate.pre_fix:
        return None, "the gate retained no pre-fix tree for the step's fix turn"
    first = min(gate.pre_fix, key=lambda entry: entry.round)
    if first.tree_digest is None:
        return None, f"the pre-fix tree of round {first.round} has no digest"
    return first.tree_digest, None


def correctness_values(inputs: StepInputs, gate: StepGate, grades: Grades) -> dict[str, Measured]:
    digest, why = _first_digest(gate)
    if digest is None:
        status = _NA if why == NO_FIX_ROUND else _UNAVAILABLE
        return same_for(NAMES, Measured.gap(status, why or NO_FIX_ROUND))
    if inputs.record is None:
        return same_for(NAMES, Measured.gap(_UNAVAILABLE, "the verifier did not grade this step"))
    final = inputs.record.grade.pass_rate()
    if grades.reader is None:
        reason = "no Assessment store to read the pre-fix grade from"
        return same_for(NAMES, Measured.gap(_UNAVAILABLE, reason))
    grade = grades.reader(inputs.record.bundle.digest, inputs.step, digest)
    if grade is None:
        grades.missing.append(f"{inputs.step}:{digest[:12]}")
        reason = (
            f"pre-fix snapshot {digest[:12]} is not graded yet (make experiment-assess grades it)"
        )
        return same_for(NAMES, Measured.gap(_UNAVAILABLE, reason))
    before = grade.pass_rate()
    if before is None or final is None:
        reason = "no graded case (infrastructure failure or no case) in the final or pre-fix grade"
        return same_for(NAMES, Measured.gap(_INSUFFICIENT, reason))
    return {PRE_FIX_PASS_RATE: Measured(before), CORRECTNESS_DELTA: Measured(final - before)}
