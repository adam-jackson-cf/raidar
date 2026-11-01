"""correctness@10 behaviour: values equal the verifier step records, gaps are statused."""

from __future__ import annotations

from dataclasses import replace

import pytest

from raidar.grading.errata import ErrataOutcome
from raidar.grading.models import (
    CaseGroup,
    CaseResult,
    CaseStatus,
    InfraFailure,
    VerifierGrade,
)
from raidar.scoring.correctness import correctness_scorer
from raidar.scoring.framework.context import AssessContext
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from raidar.scoring.framework.validation import validate_result
from tests.scorer_support import MemoryStep, memory_context, memory_step

P, F, E, S = CaseStatus.PASSED, CaseStatus.FAILED, CaseStatus.ERROR, CaseStatus.SKIPPED
CLEAN = {"oracle_leak": 0.0}


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


def _graded(step: str, grade: VerifierGrade, rewards: dict[str, float] | None = None) -> MemoryStep:
    """A checkpoint carrying ``grade`` the way the verifier records it."""
    return MemoryStep(step, grade, rewards if rewards is not None else grade.rewards() | CLEAN)


def _assess(ctx: AssessContext) -> AssessResult:
    scorer = correctness_scorer()
    return validate_result(scorer.manifest, ctx, scorer.assess(ctx))


def _by_key(result: AssessResult) -> dict[tuple[str, str], Observation]:
    return {(item.metric, item.subject): item for item in result.observations}


def test_checkpoint_values_equal_the_oracle_record() -> None:
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
    ctx = memory_context([_graded(step, grade) for step, grade in grades.items()])
    observed = _by_key(_assess(ctx))

    for step in grades:
        record = ctx.step(step).record
        assert record is not None
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


def test_missing_verdicts_are_statused_never_zero() -> None:
    infra = VerifierGrade(
        step="checkpoint_2",
        checkpoint="checkpoint_2",
        cases=(),
        infra_failure=InfraFailure(code="sandbox_timeout"),
    )
    leaked = _grade("checkpoint_3", {CaseGroup.CORE: (P,)})
    ctx = memory_context(
        [
            memory_step("checkpoint_1", (P,)),
            _graded("checkpoint_2", infra),
            _graded("checkpoint_3", leaked, {"infrastructure_failure": 0.0, "oracle_leak": 1.0}),
            memory_step("checkpoint_4", None),
        ],
        planned=["checkpoint_5"],
    )
    observed = _by_key(_assess(ctx))

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
    progression: str, solved_checkpoints: int | None
) -> None:
    ctx = memory_context(
        [memory_step("checkpoint_1", (P,)), memory_step("checkpoint_2", (F,))],
        planned=["checkpoint_3"],
        config={"progression": progression},
    )
    observed = _by_key(_assess(ctx))

    assert observed["reached", "trajectory"].value == 1
    count = observed["solved_checkpoints", "trajectory"]
    assert count.value == solved_checkpoints
    if solved_checkpoints is None:
        # Under continue, an unreached checkpoint means the trial stopped abnormally;
        # a stop right after a failure is named so the halt config can be supplied.
        assert count.status is AssessStatus.UNAVAILABLE
        assert "stopped after failed checkpoint checkpoint_2" in (count.reason or "")


def test_a_halt_run_gated_on_the_solvable_verdict_scores_the_checkpoints_past_a_raw_failure() -> (
    None
):
    """Checkpoint 2 fails raw but passes its solvable set, so the run went on and solved 3."""
    core = CaseGroup.CORE
    solvable = {
        "sha256": "s",
        "steps": {f"checkpoint_{k}": [f"checkpoint_{k}::0"] for k in range(1, 6)},
    }
    ctx = memory_context(
        [
            _graded("checkpoint_1", _grade("checkpoint_1", {core: (P,)})),
            _graded("checkpoint_2", _grade("checkpoint_2", {core: (P, F)})),  # raw: failed
            _graded("checkpoint_3", _grade("checkpoint_3", {core: (P,)})),
            _graded("checkpoint_4", _grade("checkpoint_4", {core: (F,)})),  # solvable: failed
        ],
        planned=["checkpoint_5"],
        config={"progression": "halt_on_failure", "solvable": {"toy": solvable}},
    )

    observed = _by_key(_assess(ctx))

    assert observed["solved_checkpoints_solvable", "trajectory"].value == 3
    assert observed["reached_solvable", "trajectory"].value == 3
    assert observed["solved_solvable", "checkpoint_3"].value == 1.0  # past the raw failure
    # Raw solved count: the stop is the solvable one, so the executed prefix is counted.
    assert observed["solved_checkpoints", "trajectory"].value == 2


def test_trajectory_counts_on_complete_runs() -> None:
    ctx = memory_context(
        [
            memory_step("checkpoint_1", (P,)),
            memory_step("checkpoint_2", (F,)),
            memory_step("checkpoint_3", (P,)),
        ]
    )
    observed = _by_key(_assess(ctx))
    assert observed["reached", "trajectory"].value == 1
    assert observed["solved_checkpoints", "trajectory"].value == 2

    all_solved = _by_key(_assess(memory_context([memory_step("checkpoint_1", (P,))])))
    assert all_solved["reached", "trajectory"].value == 1


def test_ungraded_trials_and_bad_config_produce_statused_results() -> None:
    steps = [memory_step("checkpoint_1", (P,))]
    bad = _assess(memory_context(steps, config={"progression": "sometimes"}))
    assert bad.status is AssessStatus.ERROR
    assert {item.status for item in bad.observations} == {AssessStatus.ERROR}

    result = _assess(memory_context([memory_step("checkpoint_1", None)]))
    assert result.status is AssessStatus.UNAVAILABLE
    assert "no checkpoint" in (result.reason or "")


def _errata_config(sha: str = "a" * 64, *excluded: str) -> dict[str, object]:
    return {"errata": {"toy": {"sha256": sha, "exclude_from_solved": list(excluded)}}}


def test_a_pinned_errata_recomputes_the_verdict_beside_the_raw_one() -> None:
    core = CaseGroup.CORE
    ctx = memory_context(
        [
            _graded("checkpoint_1", _grade("checkpoint_1", {core: (P, F)})),
            _graded("checkpoint_2", _grade("checkpoint_2", {core: (P,)})),
            memory_step("checkpoint_3", None),
        ],
        config=_errata_config("a" * 64, "core::1"),
    )
    observed = _by_key(_assess(ctx))

    assert observed["solved", "checkpoint_1"].value == 0.0
    assert observed["solved_errata", "checkpoint_1"].value == 1.0
    assert observed["solved_errata", "checkpoint_2"].value == 1.0
    ungraded = observed["solved_errata", "checkpoint_3"]
    assert (ungraded.status, ungraded.value) == (AssessStatus.UNAVAILABLE, None)


def test_without_a_pinned_errata_the_errata_metrics_are_not_applicable() -> None:
    ctx = memory_context([memory_step("checkpoint_1", (F,))])
    item = _by_key(_assess(ctx))["solved_errata", "checkpoint_1"]
    assert (item.status, item.value) == (AssessStatus.NOT_APPLICABLE, None)


def test_records_graded_under_another_errata_make_the_errata_metrics_ineligible() -> None:
    ctx = memory_context(
        [memory_step("checkpoint_1", (P,)), memory_step("checkpoint_2", (P,))],
        config=_errata_config("a" * 64),
    )
    other = ErrataOutcome(sha256="b" * 64, excluded_cases=(), excluded_failing=(), solved=True)
    first = ctx.steps[0]
    assert first.record is not None
    recorded = first.record.model_copy(update={"errata": other})
    steps = (replace(first, record=recorded), *ctx.steps[1:])
    observed = _by_key(_assess(replace(ctx, steps=steps)))

    for step in ("checkpoint_1", "checkpoint_2"):
        item = observed["solved_errata", step]
        assert (item.status, item.value) == (AssessStatus.INELIGIBLE, None)
    assert observed["solved", "checkpoint_1"].value == 1.0
