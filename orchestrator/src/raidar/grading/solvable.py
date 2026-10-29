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

    def gate_rewards(self, step: str, grade: VerifierGrade) -> dict[str, float]:
        """Raw rewards plus the pinned solvable and halt-gate rewards.

        A trustworthy grade of a step with a non-empty set adds ``solvable_pass_rate`` and the
        same value as ``halt_gate``. A step the lock pins with an empty set (excluded) adds
        ``solvable_excluded`` and a clearing ``halt_gate`` but no ``solvable_pass_rate``: it is
        neither solved nor failed, so the halt gate lets the run advance without reporting a
        solvable verdict. A step with no pinned entry or an untrustworthy grade adds nothing,
        so Harbor's ``min_reward`` halts on it (fail closed).
        """
        rewards = grade.rewards()
        cases = self.steps.get(step)
        if cases is None or grade.infra_failure is not None:
            return rewards
        if not cases:
            return rewards | {EXCLUDED_REWARD_KEY: 1.0, HALT_GATE_REWARD_KEY: 1.0}
        rate = solvable_pass_rate(grade, cases)
        assert rate is not None  # a non-empty set and a trustworthy grade
        return rewards | {SOLVABLE_REWARD_KEY: rate, HALT_GATE_REWARD_KEY: rate}


def case_key(case: CaseResult) -> str:
    """The solvable-set id of a case: ``<checkpoint_origin>::<test name without its file>``."""
    return f"{case.checkpoint_origin}::{case.id.split('::', 1)[-1]}"


def _solvable_passed(grade: VerifierGrade, cases: tuple[str, ...]) -> list[bool] | None:
    if grade.infra_failure is not None or not cases:
        return None
    passed = {case_key(case) for case in grade.cases if case.status == CaseStatus.PASSED}
    return [case in passed for case in cases]


def solvable_solved(grade: VerifierGrade, cases: tuple[str, ...]) -> bool | None:
    """Whether every solvable case passed; None without a set or without a trustworthy grade.

    A solvable case the grade did not run or skipped did not pass.
    """
    outcomes = _solvable_passed(grade, cases)
    return None if outcomes is None else all(outcomes)


SOLVABLE_REWARD_KEY = "solvable_pass_rate"
"""The step's solvable verdict: the share of its solvable cases that passed (never for excluded)."""

HALT_GATE_REWARD_KEY = "halt_gate"
"""The step reward a halt run gates on: the solvable rate, or a clearing value for an excluded
step. It is a gate signal, never a verdict an analysis may read as solved."""

EXCLUDED_REWARD_KEY = "solvable_excluded"
"""Present (1.0) exactly when the lock pins the step with an empty solvable set."""


def solvable_pass_rate(grade: VerifierGrade, cases: tuple[str, ...]) -> float | None:
    """Share of the solvable cases that passed; None exactly when ``solvable_solved`` is None.

    ``1.0`` is the solvable verdict "solved"; an unavailable value is omitted, never zero.
    The separate ``halt_gate`` clears only an explicitly pinned exclusion.
    """
    outcomes = _solvable_passed(grade, cases)
    return None if outcomes is None else sum(outcomes) / len(outcomes)


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
