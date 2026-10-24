"""The fix rounds' correctness: the pre-fix snapshot's grade against the step's final grade.

``pre_fix_pass_rate`` is the case pass rate of the workspace the step's first fix session
started from (guardrail-gate@3's pre-fix snapshot, graded offline into the Assessment store by
``raidar.grading.prefix.grade``) and ``correctness_delta`` the step's final case pass rate (its
verifier record) minus it. A step without a fix round, and every step of a shadow gate, is
``not_applicable``; an ungraded snapshot is ``unavailable`` (the Assessment is then provisional
and a later rescore, after grading, supersedes it).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.measured import Measured, same_for

_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
PRE_FIX_KEY = "pre_fix_snapshots"
NO_FIX_ROUND = "no fix round in this step: no pre-fix snapshot to compare"

GradeReader = Callable[[str, str, str], Any]


def _pre_fix_digest(record: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(digest of the first fix's starting tree, why there is none)."""
    entries = [e for e in record.get(PRE_FIX_KEY) or [] if isinstance(e, dict)]
    if not entries:
        return None, NO_FIX_ROUND
    first = min(entries, key=lambda entry: int(entry.get("round") or 0))
    digest = first.get("tree_digest")
    if not isinstance(digest, str):
        return None, f"the pre-fix snapshot was not taken: {first.get('error')}"
    return digest, None


@dataclass
class Grades:
    """The pre-fix grade reader, and the snapshots it found no grade for."""

    reader: GradeReader | None
    missing: list[str] = field(default_factory=list)


def correctness_values(
    inputs: StepInputs, record: Mapping[str, Any], grades: Grades
) -> dict[str, Measured]:
    names = ("pre_fix_pass_rate", "correctness_delta")
    digest, why = _pre_fix_digest(record)
    if digest is None:
        status = _NA if why == NO_FIX_ROUND else _UNAVAILABLE
        return same_for(names, Measured.gap(status, why or NO_FIX_ROUND))
    if inputs.record is None:
        return same_for(names, Measured.gap(_UNAVAILABLE, "the verifier did not grade this step"))
    final = inputs.record.grade.pass_rate()
    if grades.reader is None:
        reason = "no Assessment store to read the pre-fix grade from"
        return same_for(names, Measured.gap(_UNAVAILABLE, reason))
    grade = grades.reader(inputs.record.bundle.digest, inputs.step, digest)
    if grade is None:
        grades.missing.append(f"{inputs.step}:{digest[:12]}")
        reason = (
            f"pre-fix snapshot {digest[:12]} is not graded yet (make experiment-assess grades it)"
        )
        return same_for(names, Measured.gap(_UNAVAILABLE, reason))
    before = grade.pass_rate()
    if before is None or final is None:
        reason = "no graded case (infrastructure failure or no case) in the final or pre-fix grade"
        return same_for(names, Measured.gap(_INSUFFICIENT, reason))
    return {"pre_fix_pass_rate": Measured(before), "correctness_delta": Measured(final - before)}
