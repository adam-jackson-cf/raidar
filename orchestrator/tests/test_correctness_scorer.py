"""correctness@1 behaviour: values equal the verifier step records, gaps are statused."""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    InfraFailure,
    VerifierGrade,
)
from raidar.grading.records import load_step_record, step_record_dir, write_step_record
from raidar.scoring.catalog.builtin import default_scorer_registry
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.trial_inputs import load_trial_context
from raidar.scoring.framework.validation import validate_result
from tests.scorer_support import APP_V1, StepFixture, write_trial

P, F, E, S = CaseStatus.PASSED, CaseStatus.FAILED, CaseStatus.ERROR, CaseStatus.SKIPPED


def _case(step: str, index: int, group: CaseGroup, status: CaseStatus) -> CaseResult:
    origin = "checkpoint_1" if group is CaseGroup.REGRESSION else step
    return CaseResult(
        id=f"{group.value}::{index}", group=group, checkpoint_origin=origin, status=status
    )


def _grade(step: str, cases: dict[CaseGroup, tuple[CaseStatus, ...]]) -> VerifierGrade:
    results = tuple(
        _case(step, index, group, status)
        for group, statuses in cases.items()
        for index, status in enumerate(statuses)
    )
    return VerifierGrade(step=step, checkpoint=step, cases=results)


def _set_grade(
    trial: Path, step: str, grade: VerifierGrade, rewards: dict[str, float] | None = None
) -> None:
    """Replace a stored record's grade the way the verifier writes it."""
    directory = step_record_dir(trial, step)
    record = load_step_record(directory)
    rewards = rewards if rewards is not None else grade.rewards() | {"oracle_leak": 0.0}
    write_step_record(
        directory,
        record.model_copy(update={"grade": grade, "summary": grade.summary(), "rewards": rewards}),
    )


def _trial(tmp_path: Path, steps: list[str], *, graded: bool = True, planned=()) -> Path:
    statuses = (P,) if graded else None
    return write_trial(
        tmp_path / "trial",
        [StepFixture(name, APP_V1, statuses) for name in steps],
        planned=planned,
    )


def _assess(ctx: AssessContext) -> AssessResult:
    scorer = correctness_scorer()
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def test_checkpoint_values_equal_the_oracle_record(tmp_path: Path) -> None:
    trial = _trial(tmp_path, ["checkpoint_1", "checkpoint_2"])
    grades = {
        "checkpoint_1": _grade(
            "checkpoint_1",
            {CaseGroup.CORE: (P, P), CaseGroup.FUNCTIONALITY: (P, S), CaseGroup.ERROR: (P,)},
        ),
        "checkpoint_2": _grade(
            "checkpoint_2",
            {
                CaseGroup.CORE: (P, F),
                CaseGroup.FUNCTIONALITY: (P, P, S),
                CaseGroup.ERROR: (E,),
                CaseGroup.REGRESSION: (P, F, E, S),
            },
        ),
    }
    for step, grade in grades.items():
        _set_grade(trial, step, grade)
    observed = _by_key(_assess(load_trial_context(trial)))

    for step in grades:
        record = load_step_record(step_record_dir(trial, step))
        assert observed["solved", step].value == float(record.summary["solved"])
        assert observed["strict_pass_rate", step].value == record.summary["strict_pass_rate"]
        for key in ("strict_pass_rate", "core_pass_rate", "regression_pass_rate"):
            if key in record.rewards:
                assert observed[key, step].value == record.rewards[key]
        counts = record.summary["group_counts"]
        assert isinstance(counts, dict)
        for group, count in counts.items():
            item = observed[f"{group}_pass_rate", step]
            if count["total"]:
                assert (item.numerator, item.denominator) == (count["passed"], count["total"])
            else:
                assert item.status is AssessStatus.NOT_APPLICABLE
    # Skipped cases leave the denominator; failed and errored regression cases both count.
    assert observed["regression_failures", "checkpoint_2"].value == 2
    assert observed["regression_failures", "checkpoint_1"].status is AssessStatus.NOT_APPLICABLE
    assert observed["solved_checkpoints", "trajectory"].value == 1
    assert observed["reached", "trajectory"].value == 1


def test_missing_verdicts_are_statused_never_zero(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "trial",
        [
            StepFixture("checkpoint_1", APP_V1, (P,)),
            StepFixture("checkpoint_2", APP_V1, (P,)),
            StepFixture("checkpoint_3", APP_V1, (P,)),
            StepFixture("checkpoint_4", APP_V1, None),
        ],
        planned=["checkpoint_5"],
    )
    infra = VerifierGrade(
        step="checkpoint_2",
        checkpoint="checkpoint_2",
        cases=(),
        infra_failure=InfraFailure(code="sandbox_timeout"),
    )
    _set_grade(trial, "checkpoint_2", infra)
    leaked = _grade("checkpoint_3", {CaseGroup.CORE: (P,)})
    _set_grade(trial, "checkpoint_3", leaked, {"infrastructure_failure": 0.0, "oracle_leak": 1.0})
    observed = _by_key(_assess(load_trial_context(trial)))

    expected = {
        "checkpoint_2": (AssessStatus.UNAVAILABLE, "sandbox_timeout"),
        "checkpoint_3": (AssessStatus.INELIGIBLE, "leak"),
        "checkpoint_4": (AssessStatus.UNAVAILABLE, "did not grade"),
        "checkpoint_5": (AssessStatus.NOT_APPLICABLE, "not reached"),
    }
    for step, (status, reason) in expected.items():
        for metric in ("solved", "strict_pass_rate", "core_pass_rate", "regression_failures"):
            item = observed[metric, step]
            assert (item.status, item.value) == (status, None)
            assert reason in (item.reason or "")
    assert observed["solved", "checkpoint_1"].value == 1.0
    assert observed["reached", "trajectory"].status is AssessStatus.UNAVAILABLE
    assert observed["solved_checkpoints", "trajectory"].status is AssessStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("progression", "solved_checkpoints"),
    [("continue", None), ("halt_on_failure", 1)],
)
def test_trajectory_counts_follow_the_progression(
    tmp_path: Path, progression: str, solved_checkpoints: int | None
) -> None:
    trial = write_trial(
        tmp_path / "trial",
        [StepFixture("checkpoint_1", APP_V1, (P,)), StepFixture("checkpoint_2", APP_V1, (F,))],
        planned=["checkpoint_3"],
    )
    observed = _by_key(_assess(load_trial_context(trial, config={"progression": progression})))

    assert observed["reached", "trajectory"].value == 1
    count = observed["solved_checkpoints", "trajectory"]
    assert count.value == solved_checkpoints
    if solved_checkpoints is None:
        # Under continue, an unreached checkpoint means the trial stopped abnormally;
        # a stop right after a failure is named so the halt config can be supplied.
        assert count.status is AssessStatus.UNAVAILABLE
        assert "stopped after failed checkpoint checkpoint_2" in (count.reason or "")


def test_trajectory_counts_on_complete_runs(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "trial",
        [
            StepFixture("checkpoint_1", APP_V1, (P,)),
            StepFixture("checkpoint_2", APP_V1, (F,)),
            StepFixture("checkpoint_3", APP_V1, (P,)),
        ],
    )
    observed = _by_key(_assess(load_trial_context(trial)))
    assert observed["reached", "trajectory"].value == 1
    assert observed["solved_checkpoints", "trajectory"].value == 2

    all_solved = _by_key(_assess(load_trial_context(_trial(tmp_path / "b", ["checkpoint_1"]))))
    assert all_solved["reached", "trajectory"].value == 1


def test_ungraded_trials_and_bad_config_produce_statused_results(tmp_path: Path) -> None:
    trial = write_trial(tmp_path / "trial", [StepFixture("checkpoint_1", APP_V1, (P,))])
    bad = _assess(load_trial_context(trial, config={"progression": "sometimes"}))
    assert bad.status is AssessStatus.ERROR
    assert {item.status for item in bad.observations} == {AssessStatus.ERROR}

    ctx = load_trial_context(trial)
    ungraded = AssessContext(
        problem=ctx.problem,
        steps=tuple(
            step.__class__(**{**step.__dict__, "record": None, "record_digest": None})
            for step in ctx.steps
        ),
    )
    result = _assess(ungraded)
    assert result.status is AssessStatus.UNAVAILABLE
    assert "no checkpoint" in (result.reason or "")


def test_correctness_is_bound_in_the_default_registry() -> None:
    registration = default_scorer_registry().get("correctness@1")
    assert registration.load().manifest == registration.manifest
    assert registration.implementation_digest() is not None
