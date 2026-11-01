"""Solvable-set solve metrics: lock-pinned sets, never-zero gaps, raw beside them."""

from __future__ import annotations

from typing import Any

from raidar.grading.models import CaseStatus
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.framework.validation import validate_result
from tests.scorer_support import memory_context, memory_step

P, F = CaseStatus.PASSED, CaseStatus.FAILED


def _trial(
    statuses: list[tuple[CaseStatus, ...]], config: dict[str, Any], planned: list[str] | None = None
) -> AssessContext:
    steps = [memory_step(f"checkpoint_{index + 1}", step) for index, step in enumerate(statuses)]
    return memory_context(steps, planned=planned or (), config=config)


def _observed(ctx: AssessContext) -> dict[tuple[str, str], Observation]:
    scorer = correctness_scorer()
    result = validate_result(scorer.manifest, ctx, scorer.assess(ctx))
    return {(item.metric, item.subject): item for item in result.observations}


def _solvable(steps: dict[str, list[str]]) -> dict[str, Any]:
    return {"toy": {"sha256": "a" * 64, "steps": steps}}


def test_a_checkpoint_is_solved_when_every_solvable_case_passes() -> None:
    # case_1 fails in checkpoints 2 and 3; it is outside checkpoint 2's solvable set only.
    config = _solvable(
        {
            "checkpoint_1": ["checkpoint_1::case_0", "checkpoint_1::case_1"],
            "checkpoint_2": ["checkpoint_2::case_0"],
            "checkpoint_3": ["checkpoint_3::case_0", "checkpoint_3::case_1"],
        }
    )
    observed = _observed(_trial([(P, P), (P, F), (P, F)], {"solvable": config}))
    solvable = [observed["solved_solvable", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    raw = [observed["solved", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    assert solvable == [1.0, 1.0, 0.0]
    assert raw == [1.0, 0.0, 0.0]
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 2
    assert observed["solved_checkpoints", TRAJECTORY_SUBJECT].value == 1
    assert observed["reached_solvable", TRAJECTORY_SUBJECT].value == 2


def test_a_missing_solvable_set_is_unavailable_never_zero() -> None:
    for config in ({}, {"solvable": _solvable({"checkpoint_1": ["checkpoint_1::case_0"]})}):
        observed = _observed(_trial([(P,), (F,)], config))
        assert observed["solved_solvable", "checkpoint_2"].status is AssessStatus.UNAVAILABLE
        assert observed["solved_solvable", "checkpoint_2"].value is None
        trajectory = observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT]
        assert trajectory.status is AssessStatus.UNAVAILABLE
        assert observed["solved", "checkpoint_2"].value == 0.0


def test_an_empty_solvable_set_has_no_verdict() -> None:
    observed = _observed(_trial([(F,)], {"solvable": _solvable({"checkpoint_1": []})}))
    assert observed["solved_solvable", "checkpoint_1"].status is AssessStatus.NOT_APPLICABLE
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].status is (
        AssessStatus.UNAVAILABLE
    )


def test_an_excluded_step_is_skipped_and_leaves_the_usable_steps_scored() -> None:
    # Checkpoint 2 is pinned with an empty set (excluded); 1 and 3 are solved.
    config = _solvable(
        {
            "checkpoint_1": ["checkpoint_1::case_0"],
            "checkpoint_2": [],
            "checkpoint_3": ["checkpoint_3::case_0"],
        }
    )
    observed = _observed(_trial([(P,), (F,), (P,)], {"solvable": config}))
    assert observed["solved_solvable", "checkpoint_2"].status is AssessStatus.NOT_APPLICABLE
    assert observed["solved_solvable", "checkpoint_3"].value == 1.0
    solved = observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT]
    assert solved.value == 2
    assert observed["reached_solvable", TRAJECTORY_SUBJECT].value == 2


def test_a_solvable_case_the_grade_lacks_did_not_pass() -> None:
    config = {"solvable": _solvable({"checkpoint_1": ["checkpoint_1::gone"]})}
    observed = _observed(_trial([(P,)], config))
    assert observed["solved_solvable", "checkpoint_1"].value == 0.0


def test_halted_trials_leave_unattempted_checkpoints_unsolved() -> None:
    config = {
        "progression": "halt_on_failure",
        "solvable": _solvable(
            {
                "checkpoint_1": ["checkpoint_1::case_0"],
                "checkpoint_2": ["checkpoint_2::case_0"],
                "checkpoint_3": ["checkpoint_3::case_0"],
            }
        ),
    }
    observed = _observed(_trial([(P,), (F,)], config, planned=["checkpoint_3"]))
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 1
    assert observed["solved_solvable", "checkpoint_3"].status is AssessStatus.NOT_APPLICABLE


def test_a_halt_cannot_hide_a_missing_planned_solvable_set() -> None:
    config = {
        "progression": "halt_on_failure",
        "solvable": _solvable({"checkpoint_1": ["checkpoint_1::case_0"]}),
    }
    observed = _observed(_trial([(F,)], config, planned=["checkpoint_2"]))
    trajectory = observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT]
    assert trajectory.status is AssessStatus.UNAVAILABLE
    assert trajectory.value is None
    assert observed["solved_solvable", "checkpoint_1"].value == 0.0


def test_a_halt_before_an_excluded_planned_step_leaves_the_usable_steps_scored() -> None:
    config = {
        "progression": "halt_on_failure",
        "solvable": _solvable({"checkpoint_1": ["checkpoint_1::case_0"], "checkpoint_2": []}),
    }
    observed = _observed(_trial([(F,)], config, planned=["checkpoint_2"]))
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 0
    assert observed["solved_solvable", "checkpoint_2"].status is AssessStatus.NOT_APPLICABLE


def test_a_halt_run_that_executed_an_excluded_step_scores_the_steps_after_it() -> None:
    """The halt gate clears an excluded step, so the run reaches and scores what follows."""
    config = {
        "progression": "halt_on_failure",
        "solvable": _solvable(
            {
                "checkpoint_1": [],
                "checkpoint_2": ["checkpoint_2::case_0"],
                "checkpoint_3": ["checkpoint_3::case_0"],
            }
        ),
    }
    observed = _observed(_trial([(F,), (P,), (P,)], config))
    assert observed["solved_solvable", "checkpoint_1"].status is AssessStatus.NOT_APPLICABLE
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 2
    assert observed["reached_solvable", TRAJECTORY_SUBJECT].value == 2
