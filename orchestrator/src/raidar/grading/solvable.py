"""Solvable-set verdicts: the strict solve rule over the cases the reference solves.

Validation reruns the reference solution and records, per step, its **solvable set**: the
cases the reference passes in every repeat, including that step's regression cases from
earlier checkpoints (``raidar.design.problem.validation``). A checkpoint is *solved* when
every case in its solvable set passes. Cases outside the set (nondeterministic or failed by
the reference itself) never count against an agent.

The lock pins the sets (``SolvableRef``); scorers recompute the verdict from the raw cases
with ``solvable_solved``. A step without a solvable set has no verdict, never a failed one.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from raidar.grading.models import CaseResult, CaseStatus, VerifierGrade, VerifierModel

EXCLUDED_STEP_REASON = "step excluded from the solvable set: it has no new scoreable tests"
"""Why a step the lock pins with an empty solvable set has no verdict (it is skipped)."""


class SolvableRef(VerifierModel):
    """The solvable sets a problem's lock pins: the validation digest and cases per step."""

    sha256: str = Field(min_length=1)
    steps: dict[str, tuple[str, ...]] = {}


def case_key(case: CaseResult) -> str:
    """The solvable-set id of a case: ``<checkpoint_origin>::<test name without its file>``."""
    return f"{case.checkpoint_origin}::{case.id.split('::', 1)[-1]}"


def solvable_solved(grade: VerifierGrade, cases: tuple[str, ...]) -> bool | None:
    """Whether every solvable case passed; None without a set or without a trustworthy grade.

    A solvable case the grade did not run or skipped did not pass.
    """
    if grade.infra_failure is not None or not cases:
        return None
    passed = {case_key(case) for case in grade.cases if case.status == CaseStatus.PASSED}
    return all(case in passed for case in cases)


def solvable_ref_from_metadata(metadata: object) -> SolvableRef | None:
    """The ``SolvableRef`` a lock's problem metadata pins, or None when it pins none."""
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("solvable"), Mapping):
        return None
    pinned = metadata["solvable"]
    steps = pinned.get("steps")
    return SolvableRef(
        sha256=str(pinned["sha256"]),
        steps={
            str(step): tuple(str(case) for case in cases)
            for step, cases in (steps.items() if isinstance(steps, Mapping) else ())
        },
    )
