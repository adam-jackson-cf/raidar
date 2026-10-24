"""Reducing a trajectory's record and Assessments to analysis outcomes (WS-J J1).

Covers the strict and continue views of a halted trial, the headline
Metric applied to scorer components, and the rule that a gap is never
read as zero.
"""

from __future__ import annotations

import pytest

from raidar.design.experiments.metrics import (
    SCB_CANONICAL,
    SCB_CANONICAL_ONESHOT,
    SCB_CANONICAL_ONESHOT_V2,
    SCB_CANONICAL_V2,
)
from raidar.engine.trials.models import (
    CheckpointState,
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    StepResult,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.evaluation.analysis.assessed import (
    CODE_QUALITY_REF,
    CORRECTNESS_REF,
    RESOURCE_USAGE_REF,
    build_outcome,
    headline_score,
)
from raidar.evaluation.analysis.outcomes import OUTCOME_METRICS, TRUNCATED, UNASSESSED, Value
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

STEPS = ("checkpoint_1", "checkpoint_2", "checkpoint_3")
HASHES = TrialHashes(
    lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
)


def _record(
    mode: str = "continue",
    *,
    steps: tuple[str, ...] = STEPS,
    valid: bool = True,
    infra_step: str | None = None,
) -> TrialRecord:
    step_results = tuple(
        StepResult(
            trial_id="t0",
            arm="guided",
            attempt=0,
            problem="toy@1",
            index=index,
            step=step,
            checkpoint=step,
            checkpoint_state=(
                CheckpointState.INFRA_ERROR if step == infra_step else CheckpointState.SOLVED
            ),
            infra_reasons=("oracle:no_graded_cases",) if step == infra_step else (),
        )
        for index, step in enumerate(steps)
    )
    return TrialRecord(
        experiment="e",
        job_id="r",
        trial=PlannedTrial(
            trial_id="t0",
            attempt=0,
            order=0,
            sequence=0,
            arm="guided",
            problem="toy@1",
            problem_index=0,
            steps=steps,
            hashes=HASHES,
        ),
        trial_dir="trials/t0",
        progression=ProgressionRule(
            mode=mode,  # type: ignore[arg-type]
            reward_key="solved",
            threshold=1.0 if mode == "halt_on_failure" else None,
        ),
        step_results=step_results,
        reached=tuple(True for _ in steps),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="steps_started", passed=valid),)),
    )


def _ok(metric: str, subject: str, value: float) -> Observation:
    return Observation(metric=metric, subject=subject, status=AssessStatus.OK, value=value)


def _gap(metric: str, subject: str, status: AssessStatus, reason: str) -> Observation:
    return Observation(metric=metric, subject=subject, status=status, reason=reason)


def _halted_correctness() -> AssessResult:
    """Solved checkpoint 1, failed checkpoint 2, never attempted checkpoint 3."""
    return AssessResult(
        status=AssessStatus.OK,
        observations=(
            _ok("solved", "checkpoint_1", 1.0),
            _ok("solved", "checkpoint_2", 0.0),
            _gap("solved", "checkpoint_3", AssessStatus.NOT_APPLICABLE, "checkpoint not reached"),
            _ok("strict_pass_rate", "checkpoint_1", 1.0),
            _ok("strict_pass_rate", "checkpoint_2", 0.5),
            _ok("solved_checkpoints", TRAJECTORY_SUBJECT, 1.0),
            _ok("reached", TRAJECTORY_SUBJECT, 1.0),
        ),
    )


def _quality(*, rework: Observation | None = None) -> AssessResult:
    return AssessResult(
        status=AssessStatus.OK,
        observations=(
            _ok("problem_verbosity", TRAJECTORY_SUBJECT, 0.8),
            _ok("problem_erosion", TRAJECTORY_SUBJECT, 0.6),
            _ok("problem_architecture", TRAJECTORY_SUBJECT, 1.0),
            rework or _ok("problem_rework", TRAJECTORY_SUBJECT, 0.5),
            _ok("problem_regression", TRAJECTORY_SUBJECT, 0.9),
            _ok("verbosity", "checkpoint_1", 0.7),
        ),
    )


def test_halted_trial_reads_strict_and_continue_views() -> None:
    results = {CORRECTNESS_REF: _halted_correctness(), CODE_QUALITY_REF: _quality()}
    outcome = build_outcome(_record("halt_on_failure"), results, policy=SCB_CANONICAL)
    assert outcome.metrics["solve_rate"].value == pytest.approx(1 / 3)
    assert outcome.metrics["reached"] == Value.ok(1.0)
    # Under halt the unattempted last checkpoint counts as unsolved (memo §10.1) ...
    assert outcome.metrics["final_solved"] == Value.ok(0.0)
    assert [v.value for v in outcome.series["strict_solved"]] == [1.0, 0.0, 0.0]
    assert [v.status for v in outcome.series["solved"]] == ["ok", "ok", "not_applicable"]
    # ... while under continue a missing verdict stays a gap, never a zero.
    continued = build_outcome(_record("continue"), results, policy=SCB_CANONICAL)
    assert continued.metrics["final_solved"].status == "not_applicable"


def test_a_truncated_trial_has_no_trial_level_outcome() -> None:
    results = {CORRECTNESS_REF: _halted_correctness(), CODE_QUALITY_REF: _quality()}
    record = _record(steps=STEPS[:2]).model_copy(update={"truncated_to": 2})
    outcome = build_outcome(record, results, policy=SCB_CANONICAL)
    # Its last run checkpoint is not the problem's final one, and K is not the problem's K.
    for metric in OUTCOME_METRICS:
        assert outcome.metric(metric.id).status == TRUNCATED
        assert "max_checkpoints" in (outcome.metric(metric.id).reason or "")
    # What did run is still described per checkpoint.
    assert [v.value for v in outcome.series["solved"]] == [1.0, 0.0]


def test_headline_is_the_policy_score_of_scorer_components() -> None:
    results = {CORRECTNESS_REF: _halted_correctness(), CODE_QUALITY_REF: _quality()}
    outcome = build_outcome(_record(), results, policy=SCB_CANONICAL)
    inertia = 0.15 * 0.8 + 0.15 * 0.6 + 0.20 * 1.0 + 0.30 * 0.5 + 0.20 * 0.9
    # C is the fork's mean case pass rate (1.0, 0.5, unreached 0), not solved/K (1/3).
    assert outcome.metrics["correctness"] == Value.ok(0.5)
    assert outcome.metrics["solve_rate"].value == pytest.approx(1 / 3)
    assert outcome.metrics["headline_score"].value == pytest.approx(
        100 * 0.5 * (0.7 + 0.3 * inertia), abs=1e-6
    )
    assert outcome.metrics["verbosity"] == Value.ok(0.8)
    assert outcome.series["verbosity"][0] == Value.ok(0.7)


def test_a_component_gap_makes_the_headline_ineligible_not_zero() -> None:
    rework = _gap("problem_rework", TRAJECTORY_SUBJECT, AssessStatus.INELIGIBLE, "bad inventory")
    results = {CORRECTNESS_REF: _halted_correctness(), CODE_QUALITY_REF: _quality(rework=rework)}
    headline = build_outcome(_record(), results, policy=SCB_CANONICAL).metrics["headline_score"]
    assert headline.status == "ineligible"
    assert headline.value is None
    assert "bad inventory" in (headline.reason or "")
    missing = build_outcome(
        _record(), {CORRECTNESS_REF: _halted_correctness()}, policy=SCB_CANONICAL
    )
    assert missing.metrics["headline_score"].status == UNASSESSED
    assert missing.metrics["total_cost_usd"].status == UNASSESSED


def test_one_shot_policy_drops_not_applicable_transitions() -> None:
    components = {
        "C": Value.ok(1.0),
        "V": Value.ok(0.5),
        "E": Value.ok(0.5),
        "H": Value.ok(1.0),
        "R": Value.missing("not_applicable", "one checkpoint has no transitions"),
        "G": Value.missing("not_applicable", "one checkpoint has no transitions"),
    }
    score = headline_score(SCB_CANONICAL_ONESHOT, 1, components)
    assert score.value == pytest.approx(100 * (0.7 + 0.3 * (0.3 * 0.5 + 0.3 * 0.5 + 0.4 * 1.0)))
    assert headline_score(SCB_CANONICAL, 3, components).status == "ineligible"


def test_invalid_or_infra_trials_are_flagged_with_reasons() -> None:
    usage = AssessResult(
        status=AssessStatus.OK, observations=(_ok("total_cost_usd", TRAJECTORY_SUBJECT, 0.02),)
    )
    clean = build_outcome(_record(), {RESOURCE_USAGE_REF: usage}, policy=SCB_CANONICAL)
    assert not clean.infra_error
    assert clean.metrics["total_cost_usd"] == Value.ok(0.02)
    broken = build_outcome(
        _record(valid=False, infra_step="checkpoint_2"), {}, policy=SCB_CANONICAL
    )
    assert broken.infra_error
    assert broken.infra_reasons == (
        "validity:steps_started",
        "checkpoint_2:oracle:no_graded_cases",
    )


def _failed_correctness(rate: float) -> AssessResult:
    """Every checkpoint reached and failed, each at case pass rate ``rate``."""
    return AssessResult(
        status=AssessStatus.OK,
        observations=(
            *(_ok("solved", step, 0.0) for step in STEPS),
            *(_ok("strict_pass_rate", step, rate) for step in STEPS),
            _ok("solved_checkpoints", TRAJECTORY_SUBJECT, 0.0),
            _ok("reached", TRAJECTORY_SUBJECT, 0.0),
        ),
    )


def _rework_gap() -> AssessResult:
    rework = _gap("problem_rework", TRAJECTORY_SUBJECT, AssessStatus.INELIGIBLE, "bad inventory")
    return _quality(rework=rework)


def test_v2_c_zero_scores_zero_and_keeps_the_trial_despite_quality_gaps() -> None:
    # S2-SS-2: dropping C = 0 trials would bias paired S towards the arm that fails more.
    for quality in (_rework_gap(), None):
        results = {CORRECTNESS_REF: _failed_correctness(0.0)}
        if quality is not None:
            results[CODE_QUALITY_REF] = quality
        v2 = build_outcome(_record(), results, policy=SCB_CANONICAL_V2)
        v1 = build_outcome(_record(), results, policy=SCB_CANONICAL)
        assert v2.metrics["correctness"] == Value.ok(0.0)
        assert v2.metrics["headline_score"] == Value.ok(0.0)
        # @1 is fork-exact: any quality gap leaves S unscored, even at C = 0.
        assert v1.metrics["headline_score"].value is None
        assert v1.metrics["headline_score"].status == ("ineligible" if quality else UNASSESSED)


def test_v2_c_above_zero_with_a_quality_gap_is_ineligible() -> None:
    results = {CORRECTNESS_REF: _failed_correctness(0.25), CODE_QUALITY_REF: _rework_gap()}
    outcome = build_outcome(_record(), results, policy=SCB_CANONICAL_V2)
    assert outcome.metrics["correctness"] == Value.ok(0.25)
    assert outcome.metrics["headline_score"].status == "ineligible"
    assert "bad inventory" in (outcome.metrics["headline_score"].reason or "")
    missing = build_outcome(
        _record(), {CORRECTNESS_REF: _failed_correctness(0.25)}, policy=SCB_CANONICAL_V2
    )
    assert missing.metrics["headline_score"].status == UNASSESSED


def test_v2_all_checkpoints_failed_but_partially_passing_is_not_zero() -> None:
    # solved/K is 0 here, but C (mean pass rate) is 0.9, so the zero rule does not fire.
    results = {CORRECTNESS_REF: _failed_correctness(0.9), CODE_QUALITY_REF: _quality()}
    outcome = build_outcome(_record(), results, policy=SCB_CANONICAL_V2)
    assert outcome.metrics["solve_rate"] == Value.ok(0.0)
    assert outcome.metrics["correctness"].value == pytest.approx(0.9)
    assert (outcome.metrics["headline_score"].value or 0) > 0


def test_a_reached_checkpoint_with_no_graded_case_makes_c_ineligible_not_zero() -> None:
    observations = list(_failed_correctness(0.0).observations)
    observations[3] = _gap(
        "strict_pass_rate", "checkpoint_1", AssessStatus.NOT_APPLICABLE, "no graded oracle cases"
    )
    result = AssessResult(status=AssessStatus.OK, observations=tuple(observations))
    outcome = build_outcome(
        _record(), {CORRECTNESS_REF: result, CODE_QUALITY_REF: _quality()}, policy=SCB_CANONICAL_V2
    )
    assert outcome.metrics["correctness"].status == "ineligible"
    assert "no graded oracle cases" in (outcome.metrics["correctness"].reason or "")
    assert outcome.metrics["headline_score"].status == "ineligible"


def test_c_uses_exact_case_counts_when_the_scorer_records_them() -> None:
    observations = (
        *(_ok("solved", step, 0.0) for step in STEPS),
        *(
            Observation(
                metric="strict_pass_rate",
                subject=step,
                status=AssessStatus.OK,
                value=2 / 3,
                numerator=2,
                denominator=3,
            )
            for step in STEPS
        ),
    )
    result = AssessResult(status=AssessStatus.OK, observations=observations)
    outcome = build_outcome(
        _record(), {CORRECTNESS_REF: result, CODE_QUALITY_REF: _quality()}, policy=SCB_CANONICAL
    )
    inertia = 0.15 * 0.8 + 0.15 * 0.6 + 0.20 * 1.0 + 0.30 * 0.5 + 0.20 * 0.9
    assert outcome.metrics["headline_score"].value == pytest.approx(
        100 * (2 / 3) * (0.7 + 0.3 * inertia), abs=1e-6
    )


def test_oneshot_v2_c_zero_scores_zero_without_quality() -> None:
    components = {"C": Value.ok(0.0), "V": Value.missing("ineligible", "no files")}
    assert headline_score(SCB_CANONICAL_ONESHOT_V2, 1, components) == Value.ok(0.0)
    assert headline_score(SCB_CANONICAL_ONESHOT, 1, components).status == "ineligible"
