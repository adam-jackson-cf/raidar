"""Solvable-set solve metrics: lock-pinned sets, never-zero gaps, raw beside them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raidar.grading.models import CaseStatus
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from tests.scorer_support import APP_V1, StepFixture, write_trial

P, F = CaseStatus.PASSED, CaseStatus.FAILED


def _trial(tmp_path: Path, statuses: list[tuple[CaseStatus, ...]], planned=()) -> Path:
    return write_trial(
        tmp_path / "trial",
        [
            StepFixture(f"checkpoint_{index + 1}", APP_V1, step)
            for index, step in enumerate(statuses)
        ],
        planned=planned,
    )


def _observed(trial: Path, config: dict[str, Any]) -> dict[tuple[str, str], Observation]:
    ctx = load_trial_context(trial, config=config)
    scorer = correctness_scorer()
    result = validate_result(scorer.manifest, ctx, scorer.assess(ctx))
    return {(item.metric, item.subject): item for item in result.observations}


def _solvable(steps: dict[str, list[str]]) -> dict[str, Any]:
    return {"toy": {"sha256": "a" * 64, "steps": steps}}


def test_a_checkpoint_is_solved_when_every_solvable_case_passes(tmp_path: Path) -> None:
    # case_1 fails in checkpoints 2 and 3; it is outside checkpoint 2's solvable set only.
    trial = _trial(tmp_path, [(P, P), (P, F), (P, F)])
    config = _solvable(
        {
            "checkpoint_1": ["checkpoint_1::case_0", "checkpoint_1::case_1"],
            "checkpoint_2": ["checkpoint_2::case_0"],
            "checkpoint_3": ["checkpoint_3::case_0", "checkpoint_3::case_1"],
        }
    )
    observed = _observed(trial, {"solvable": config})
    solvable = [observed["solved_solvable", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    raw = [observed["solved", f"checkpoint_{k}"].value for k in (1, 2, 3)]
    assert solvable == [1.0, 1.0, 0.0]
    assert raw == [1.0, 0.0, 0.0]
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 2
    assert observed["solved_checkpoints", TRAJECTORY_SUBJECT].value == 1
    assert observed["reached_solvable", TRAJECTORY_SUBJECT].value == 2


def test_a_missing_solvable_set_is_unavailable_never_zero(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(P,), (F,)])
    for config in ({}, {"solvable": _solvable({"checkpoint_1": ["checkpoint_1::case_0"]})}):
        observed = _observed(trial, config)
        assert observed["solved_solvable", "checkpoint_2"].status is AssessStatus.UNAVAILABLE
        assert observed["solved_solvable", "checkpoint_2"].value is None
        trajectory = observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT]
        assert trajectory.status is AssessStatus.UNAVAILABLE
        assert observed["solved", "checkpoint_2"].value == 0.0


def test_an_empty_solvable_set_has_no_verdict(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(F,)])
    observed = _observed(trial, {"solvable": _solvable({"checkpoint_1": []})})
    assert observed["solved_solvable", "checkpoint_1"].status is AssessStatus.UNAVAILABLE


def test_a_solvable_case_the_grade_lacks_did_not_pass(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(P,)])
    observed = _observed(trial, {"solvable": _solvable({"checkpoint_1": ["checkpoint_1::gone"]})})
    assert observed["solved_solvable", "checkpoint_1"].value == 0.0


def test_halted_trials_leave_unattempted_checkpoints_unsolved(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(P,), (F,)], planned=["checkpoint_3"])
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
    observed = _observed(trial, config)
    assert observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT].value == 1
    assert observed["solved_solvable", "checkpoint_3"].status is AssessStatus.NOT_APPLICABLE


def test_a_halt_cannot_hide_a_missing_planned_solvable_set(tmp_path: Path) -> None:
    trial = _trial(tmp_path, [(F,)], planned=["checkpoint_2"])
    for remainder in ({}, {"checkpoint_2": []}):
        config = {
            "progression": "halt_on_failure",
            "solvable": _solvable({"checkpoint_1": ["checkpoint_1::case_0"], **remainder}),
        }
        observed = _observed(trial, config)
        trajectory = observed["solved_checkpoints_solvable", TRAJECTORY_SUBJECT]
        assert trajectory.status is AssessStatus.UNAVAILABLE
        assert trajectory.value is None
        assert observed["solved_solvable", "checkpoint_1"].value == 0.0
