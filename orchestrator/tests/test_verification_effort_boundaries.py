"""verification-effort: evidence boundaries that leave a measure unavailable or not applicable.

A fix turn's correctness needs a retained pre-fix tree with a digest, a store to read its grade
from and a graded case on both sides; a step is unchanged only when both source trees can be
read and are equal, and totals over steps that all had nothing to measure are not applicable.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus
from raidar.scoring.framework.validation import validate_result
from raidar.scoring.verification_effort import VERIFICATION_EFFORT, verification_effort_scorer
from raidar.scoring.verification_effort.sources import unchanged_steps
from tests.verification_effort_support import (
    CP1,
    CP2,
    assess,
    context,
    fix_turn,
    graded,
    pre_fix_entry,
    seal,
    step,
    store_pre_fix_grade,
    trace,
    values,
    with_snapshot,
    write_gate,
)

UNAVAILABLE = AssessStatus.UNAVAILABLE
NA = AssessStatus.NOT_APPLICABLE


def _fixed(
    tmp_path: Path, *, pre_fix: tuple[dict[str, Any], ...], passed: int = 4, failed: int = 0
) -> StepInputs:
    item = step(tmp_path, CP1)
    write_gate(
        trace(item),
        [seal(1, "refused", "tests"), seal(2, "passed")],
        fixes=(fix_turn(2),),
        pre_fix=pre_fix,
    )
    return graded(item, passed=passed, failed=failed)


def test_a_pre_fix_tree_without_a_digest_leaves_the_fix_correctness_unavailable(
    tmp_path: Path,
) -> None:
    item = _fixed(tmp_path, pre_fix=(pre_fix_entry(digest=None),))

    gap = values(assess(item, store=tmp_path / "store"))[("correctness_delta", CP1)]

    assert gap.status is UNAVAILABLE
    assert gap.reason == "the pre-fix tree of round 1 has no digest"


def test_without_an_assessment_store_the_pre_fix_grade_cannot_be_read(tmp_path: Path) -> None:
    item = _fixed(tmp_path, pre_fix=(pre_fix_entry(),))

    gap = values(assess(item))[("correctness_delta", CP1)]

    assert gap.status is UNAVAILABLE
    assert gap.reason == "no Assessment store to read the pre-fix grade from"


def test_a_pre_fix_grade_with_no_graded_case_is_insufficient_evidence(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store_pre_fix_grade(store, CP1, passed=0, failed=0)
    item = _fixed(tmp_path, pre_fix=(pre_fix_entry(),))

    observed = values(assess(item, store=store))

    assert observed[("pre_fix_pass_rate", CP1)].status is AssessStatus.INSUFFICIENT_EVIDENCE
    assert "no graded case" in (observed[("correctness_delta", CP1)].reason or "")


def test_a_final_grade_with_no_graded_case_is_insufficient_evidence(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store_pre_fix_grade(store, CP1, passed=2, failed=2)
    item = _fixed(tmp_path, pre_fix=(pre_fix_entry(),), passed=0, failed=0)

    observed = values(assess(item, store=store))

    assert observed[("correctness_delta", CP1)].status is AssessStatus.INSUFFICIENT_EVIDENCE


def test_steps_equal_to_the_starter_are_unchanged_and_their_total_is_not_applicable(
    tmp_path: Path,
) -> None:
    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "forge.py").write_text("a = 1\n")
    item = with_snapshot(tmp_path, step(tmp_path, CP1), "a = 1\n")
    write_gate(trace(item), [seal(1, "passed")])
    ctx = dataclasses.replace(context(item), starter=starter)

    assert unchanged_steps(ctx) == frozenset({CP1})
    result = validate_result(VERIFICATION_EFFORT, ctx, verification_effort_scorer().assess(ctx))
    total = values(result)[("total_first_run_blocking", TRAJECTORY_SUBJECT)]
    assert total.status is NA
    assert total.reason == "first_run_blocking is not applicable at any gated step"


def test_a_step_is_not_unchanged_when_a_source_tree_cannot_be_read(tmp_path: Path) -> None:
    starter = tmp_path / "starter"
    starter.mkdir()
    (starter / "forge.py").write_text("a = 1\n")
    readable = with_snapshot(tmp_path, step(tmp_path, CP1), "a = 1\n")
    missing = dataclasses.replace(step(tmp_path, CP2), snapshot=tmp_path / "gone")

    assert unchanged_steps(dataclasses.replace(context(missing), starter=starter)) == frozenset()
    assert (
        unchanged_steps(
            dataclasses.replace(context(readable), starter=tmp_path / "not-a-directory")
        )
        == frozenset()
    )
