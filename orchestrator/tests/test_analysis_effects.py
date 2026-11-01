"""Analysis on synthetic trials with known effects.

Each scenario plants an effect and checks the verdict the report gives it:
improved, worse, inconclusive and insufficient, plus the exclusion counts,
cost beside the effect, the strict/continue views, curves and the J2
sample-size projection.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction

import pytest

from raidar.evaluation.analysis.effects import (
    ArmExclusions,
    ExactTestName,
    Exclusions,
    Settings,
    Verdict,
    decide,
    sign_flip_test,
)
from raidar.evaluation.analysis.markdown import render_markdown
from raidar.evaluation.analysis.outcomes import (
    DependencyStep,
    ParityComparison,
    PoolFinding,
    PoolingReport,
    Value,
)
from raidar.evaluation.analysis.report import AnalysisReport, DevRun, analyse
from raidar.evaluation.analysis.stats import IntervalMethod, summarise
from raidar.scoring.catalog.outcomes import (
    OUTCOME_METRICS,
    VERIFIER_DISAGREEMENTS,
    Kind,
    declared_kind,
    declared_outcome_types,
    outcome_metric,
)
from raidar.scoring.framework.manifest import Direction
from raidar.scoring.framework.result import AssessResult, AssessStatus, Observation
from tests.analysis_support import (
    BASELINE,
    GUIDED,
    OUTCOME_TYPES,
    gap,
    inputs,
    paired_arms,
    trial,
)

SETTINGS = Settings(
    outcome_types=OUTCOME_TYPES,
    min_pairs=5,
    max_exclusion_fraction=0.2,
    max_exclusion_difference=0.5,
    bootstrap_samples=2000,
    seed=7,
)


def _effect(report: AnalysisReport, metric: str, problem: str = "toy@1"):
    (comparison,) = report.comparisons
    return next(e for e in comparison.effects if e.metric == metric and e.problem == problem)


def _run(outcomes, attempts, settings=SETTINGS, problems=("toy@1",)) -> AnalysisReport:
    return analyse(inputs(outcomes, attempts, problems), settings)


def test_consistent_gain_is_improved_with_cost_beside() -> None:
    baseline = [0.25, 0.5, 0.25, 0.5, 0.375, 0.25]
    gains = [0.25, 0.375, 0.25, 0.5, 0.25, 0.25]
    treatment = [b + d for b, d in zip(baseline, gains, strict=True)]
    effect = _effect(_run(paired_arms("solve_rate", baseline, treatment), 6), "solve_rate")
    assert effect.verdict is Verdict.IMPROVED
    assert effect.n_pairs == 6
    assert effect.mean_delta == pytest.approx(0.3125)
    assert effect.ci is not None
    assert effect.ci.method is IntervalMethod.PAIRED_T
    assert effect.ci.low > 0
    assert effect.cost.baseline_mean == pytest.approx(1.0)
    assert effect.cost.treatment_mean == pytest.approx(1.5)
    assert effect.cost.delta_mean == pytest.approx(0.5)
    assert [pair.attempt for pair in effect.pairs] == [0, 1, 2, 3, 4, 5]


def test_consistent_loss_is_worse_and_direction_follows_the_metric() -> None:
    baseline = [4.0, 3.0, 4.0, 2.0, 3.0, 3.0]
    treatment = [1.0, 1.0, 2.0, 0.0, 1.0, 1.0]
    effect = _effect(_run(paired_arms("reached", baseline, treatment), 6), "reached")
    assert effect.verdict is Verdict.WORSE
    assert effect.ci is not None
    assert effect.ci.high < 0


def test_noise_around_zero_is_inconclusive() -> None:
    baseline = [50.0, 60.0, 55.0, 40.0, 70.0]
    treatment = [58.0, 52.0, 57.0, 44.0, 62.0]
    effect = _effect(_run(paired_arms("headline_score", baseline, treatment), 5), "headline_score")
    assert effect.verdict is Verdict.INCONCLUSIVE
    assert effect.ci is not None
    assert effect.ci.low < 0 < effect.ci.high
    assert "exact sign-flip p=" in effect.verdict_reason


def test_too_few_pairs_is_insufficient_even_with_a_large_effect() -> None:
    effect = _effect(
        _run(paired_arms("solve_rate", [0.0, 0.0, 0.0], [1.0, 0.9, 1.0]), 3), "solve_rate"
    )
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "3 pairs" in effect.verdict_reason
    assert "minimum 5" in effect.verdict_reason


def test_excluded_share_above_the_limit_is_insufficient_and_counted_by_cause() -> None:
    outcomes = paired_arms("solve_rate", [0.2] * 7, [0.8, 0.9, 0.7, 0.8, 0.9, 0.8, 0.9])
    # Attempt 5: infra-invalid treatment; attempt 6: baseline value is an ineligible gap.
    outcomes[11] = trial(GUIDED, 5, {"solve_rate": 0.9}, infra=True)
    outcomes[12] = trial(BASELINE, 6, {"solve_rate": gap("unavailable", "no verdict")})
    # Attempt 7 is planned but never ran; attempt 8's baseline was never assessed.
    outcomes.append(trial(GUIDED, 7, {"solve_rate": 0.9}))
    outcomes += [trial(BASELINE, 8, {}), trial(GUIDED, 8, {"solve_rate": 0.9})]
    for attempt in (9, 10, 11):
        outcomes += [
            trial(BASELINE, attempt, {"solve_rate": 0.2}),
            trial(GUIDED, attempt, {"solve_rate": 0.9}),
        ]
    effect = _effect(_run(outcomes, 12), "solve_rate")
    exclusions = effect.exclusions
    assert (exclusions.planned, exclusions.paired) == (12, 8)
    assert (exclusions.infra_error, exclusions.not_run, exclusions.unassessed) == (1, 1, 1)
    assert exclusions.ineligible == 1
    assert exclusions.ineligible_statuses == {"unavailable": 1}
    by_arm = {arm.arm: arm.causes for arm in exclusions.arms}
    assert by_arm == {
        BASELINE: {"ineligible:unavailable": 1, "not_run": 1, "unassessed": 1},
        GUIDED: {"infra_error:validity:steps_started": 1},
    }
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "4/12 planned pairs excluded" in effect.verdict_reason
    relaxed = Settings(
        outcome_types=OUTCOME_TYPES,
        min_pairs=5,
        max_exclusion_fraction=0.5,
        bootstrap_samples=2000,
        max_exclusion_difference=0.5,
    )
    assert _effect(_run(outcomes, 12, relaxed), "solve_rate").verdict is Verdict.IMPROVED


def test_binary_final_solved_uses_exact_mcnemar() -> None:
    # 5 discordant pairs all favouring the treatment: two-sided exact p = 0.0625.
    report = _run(paired_arms("final_solved", [0.0] * 5 + [1.0], [1.0] * 6), 6)
    effect = _effect(report, "final_solved")
    assert effect.mcnemar is not None
    assert (effect.mcnemar.treatment_only, effect.mcnemar.baseline_only) == (5, 0)
    assert effect.mcnemar.p_value == pytest.approx(0.0625)
    assert effect.verdict is Verdict.INCONCLUSIVE
    report = _run(paired_arms("final_solved", [0.0] * 6, [1.0] * 6), 6)
    assert _effect(report, "final_solved").verdict is Verdict.IMPROVED


def test_five_one_way_binary_pairs_are_inconclusive_and_six_give_a_verdict() -> None:
    five = _effect(_run(paired_arms("final_solved", [0.0] * 5, [1.0] * 5), 5), "final_solved")
    assert five.test is not None
    assert five.test.name is ExactTestName.MCNEMAR
    assert five.test.p_value == pytest.approx(0.0625)
    assert five.verdict is Verdict.INCONCLUSIVE
    assert "exact McNemar p=0.0625 > 0.05" in five.verdict_reason
    six = _effect(_run(paired_arms("final_solved", [0.0] * 6, [1.0] * 6), 6), "final_solved")
    assert six.test is not None
    assert six.test.p_value == pytest.approx(0.03125)
    assert six.verdict is Verdict.IMPROVED
    worse = _effect(_run(paired_arms("final_solved", [1.0] * 6, [0.0] * 6), 6), "final_solved")
    assert worse.verdict is Verdict.WORSE


def test_the_test_follows_the_declared_type_not_the_observed_values() -> None:
    # solve_rate is declared continuous: values that happen to be 0 and 1 still get the
    # sign-flip test, and final_solved is declared binary: fractional values still McNemar.
    rate = _effect(_run(paired_arms("solve_rate", [0.0] * 6, [1.0] * 6), 6), "solve_rate")
    assert rate.mcnemar is None
    assert rate.test is not None
    assert rate.test.name is ExactTestName.SIGN_FLIP
    assert rate.test.p_value == pytest.approx(0.03125)
    solved = _effect(_run(paired_arms("final_solved", [0.0] * 6, [0.9] * 6), 6), "final_solved")
    assert solved.test is not None
    assert solved.test.name is ExactTestName.MCNEMAR


def test_the_sign_flip_verdict_ignores_the_interval() -> None:
    # Five positive deltas: the paired-t interval excludes 0, but the exact test's smallest
    # possible p is 0.0625, so the verdict stays inconclusive.
    baseline = [0.25, 0.5, 0.25, 0.5, 0.375]
    treatment = [b + 0.25 for b in baseline]
    effect = _effect(_run(paired_arms("solve_rate", baseline, treatment), 5), "solve_rate")
    assert effect.ci is not None
    assert effect.ci.low > 0
    assert effect.test is not None
    assert effect.test.p_value == pytest.approx(0.0625)
    assert effect.verdict is Verdict.INCONCLUSIVE


def test_an_intractable_exact_enumeration_is_insufficient_never_approximated() -> None:
    baseline = [0.0] * 30
    treatment = [(index + 1) ** 0.5 for index in range(30)]
    effect = _effect(_run(paired_arms("solve_rate", baseline, treatment), 30), "solve_rate")
    assert effect.test is not None
    assert effect.test.p_value is None
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "not computable" in effect.verdict_reason


def test_bootstrap_from_ten_pairs_is_seeded_and_replays() -> None:
    baseline = [0.1 * (i % 4) for i in range(12)]
    treatment = [b + 0.2 + 0.05 * (i % 3) for i, b in enumerate(baseline)]
    first = _run(paired_arms("solve_rate", baseline, treatment), 12)
    effect = _effect(first, "solve_rate")
    assert effect.ci is not None
    assert effect.ci.method is IntervalMethod.BOOTSTRAP
    assert effect.verdict is Verdict.IMPROVED
    again = _run(paired_arms("solve_rate", baseline, treatment), 12)
    assert first.model_dump_json() == again.model_dump_json()
    reseeded = Settings(
        min_pairs=5,
        bootstrap_samples=2000,
        seed=8,
        max_exclusion_difference=0.5,
        outcome_types=OUTCOME_TYPES,
    )
    other = _effect(
        _run(paired_arms("solve_rate", baseline, treatment), 12, reseeded), "solve_rate"
    )
    assert other.ci != effect.ci


@pytest.mark.parametrize(
    ("progression", "primary"),
    [("continue", "solve_rate_solvable"), ("halt_on_failure", "solve_rate_solvable")],
)
def test_primary_outcome_follows_progression_and_s_is_secondary(
    progression: str, primary: str
) -> None:
    outcomes = []
    for attempt in range(6):
        base = {
            "headline_score": 50.0,
            "solve_rate_solvable": 0.25,
            "reached_solvable": 1.0,
        }
        wiggle = attempt % 2
        treated = {
            "headline_score": 60.0 + wiggle,
            "solve_rate_solvable": 0.75 - 0.125 * wiggle,
            "reached_solvable": 3.0 - wiggle,
        }
        outcomes += [trial(BASELINE, attempt, base), trial(GUIDED, attempt, treated)]
    report = analyse(inputs(outcomes, 6, progression=progression), SETTINGS)
    (comparison,) = report.comparisons
    assert report.primary_metric == primary
    assert comparison.primary.metric == primary
    assert comparison.primary.verdict is Verdict.IMPROVED
    assert [e.metric for e in comparison.effects] == [m.id for m in OUTCOME_METRICS]
    assert _effect(report, "headline_score").view == "headline"
    assert report.metric["ref"] == "scb-canonical@1"
    # Metrics nobody measured are unassessed exclusions, never zeros; a missing
    # quality component leaves S's verdict open without touching the primary one.
    quality = _effect(report, "verbosity")
    assert quality.n_pairs == 0
    assert quality.exclusions.unassessed == 6
    assert quality.verdict is Verdict.INSUFFICIENT
    assert quality.mean_delta is None


def test_trials_of_one_problem_must_cover_the_same_checkpoints() -> None:
    outcomes = paired_arms("solve_rate", [0.2] * 5, [0.6] * 5)
    outcomes[1] = outcomes[1].model_copy(update={"steps": outcomes[1].steps[:2]})
    with pytest.raises(ValueError, match="different checkpoint lists"):
        _run(outcomes, 5)


def test_several_problems_get_an_equal_weight_aggregate() -> None:
    outcomes = paired_arms("solve_rate", [0.2] * 5, [0.6, 0.7, 0.6, 0.7, 0.6], problem="a@1")
    outcomes += paired_arms("solve_rate", [0.5] * 5, [0.6, 0.55, 0.65, 0.6, 0.6], problem="b@1")
    report = _run(outcomes, 5, problems=("a@1", "b@1"))
    per_problem = [_effect(report, "solve_rate", p) for p in ("a@1", "b@1")]
    aggregate = _effect(report, "solve_rate", "*")
    assert aggregate.mean_delta == pytest.approx(
        (per_problem[0].mean_delta + per_problem[1].mean_delta) / 2
    )
    assert aggregate.ci is not None
    assert aggregate.ci.method is IntervalMethod.PROBLEM_EQUAL_WEIGHT_T
    assert aggregate.exclusions.planned == 10
    assert aggregate.verdict is Verdict.IMPROVED
    assert aggregate.test is not None
    assert aggregate.test.name is ExactTestName.SIGN_FLIP
    assert aggregate.test.informative == 10
    assert aggregate.test.p_value == pytest.approx(2 / 1024)
    assert report.comparisons[0].primary.problem == "*"
    # J2 for the aggregate: pairs per problem from the effective SD sqrt(sum SD_p^2) / P,
    # costed as one more attempt on every problem.
    size = aggregate.sample_size
    assert size is not None
    sds = [effect.sd_delta for effect in per_problem]
    effective = (sds[0] ** 2 + sds[1] ** 2) ** 0.5 / 2
    assert size.pilot_sd == pytest.approx(effective)
    assert size.recommended_pairs == -(-7.84 * (effective / 0.125) ** 2 // 1)
    assert size.per_pair_cost_usd == pytest.approx(2 * 2.5)
    assert size.basis.startswith("equal-weight aggregate of 2 problems")

    # An aggregate of binary problems is a mean of per-problem means: its declared type is
    # continuous, so its verdict is the sign-flip test on the equal-weight terms, whatever the
    # per-problem declarations. Problem a (45 pairs, all +1) and b (55 pairs, 53 of them -1)
    # weight to a tiny positive equal-weight mean; pooled counts would point the other way.
    # Both orientations: the verdict and the mean beside it share one sign.
    for reverse in (False, True):
        low, high = (1.0, 0.0) if reverse else (0.0, 1.0)
        left = paired_arms("final_solved", [low] * 45, [high] * 45, problem="a@1")
        right = paired_arms("final_solved", [high] * 55, [low] * 53 + [high] * 2, problem="b@1")
        binary_report = _run(left + right, 55, problems=("a@1", "b@1"))
        combined = _effect(binary_report, "final_solved", "*")
        assert combined.mcnemar is None
        assert combined.kind is Kind.CONTINUOUS
        assert combined.test is not None
        assert combined.test.name is ExactTestName.SIGN_FLIP
        assert combined.mean_delta == pytest.approx(-0.018181818 if reverse else 0.018181818)
        assert (combined.test.statistic > 0) is (combined.mean_delta > 0)
        assert combined.test.p_value is not None
        assert combined.test.p_value > 0.05
        assert combined.verdict is Verdict.INCONCLUSIVE
        for problem in ("a@1", "b@1"):
            single = _effect(binary_report, "final_solved", problem)
            assert single.kind is Kind.BINARY
            assert single.mcnemar is not None


def test_curves_band_each_checkpoint_across_attempts_and_skip_invalid_trials() -> None:
    outcomes = [trial(BASELINE, b, {}, solved=[1.0, 0.0, 0.0, None]) for b in range(5)] + [
        trial(GUIDED, b, {}, solved=[1.0, 1.0, 0.0 if b else 1.0, None]) for b in range(5)
    ]
    outcomes.append(trial(GUIDED, 5, {}, infra=True, solved=[0.0, 0.0, 0.0, 0.0]))
    outcomes.append(trial(BASELINE, 5, {}, solved=[1.0, 0.0, 0.0, 0.0]))
    (comparison,) = _run(outcomes, 6).comparisons
    curve = next(c for c in comparison.curves if c.series == "solved")
    first, second, third, fourth = curve.points
    assert (first.baseline.n, first.treatment.n) == (6, 5)
    assert second.delta.mean == pytest.approx(1.0)
    assert second.delta.n == 5
    assert third.treatment.mean == pytest.approx(0.2)
    assert (fourth.treatment.n, fourth.treatment.missing) == (0, 5)
    assert fourth.baseline.n == 1
    assert fourth.baseline.ci is None
    assert {c.view for c in comparison.curves} >= {"continue", "strict"}


def test_sample_size_projection_uses_pilot_sd_and_cost() -> None:
    baseline = [0.25, 0.5, 0.25, 0.5, 0.375]
    treatment = [0.5, 0.625, 0.5, 0.75, 0.5]
    report = _run(paired_arms("solve_rate", baseline, treatment), 5)
    effect = _effect(report, "solve_rate")
    size = effect.sample_size
    assert size is not None
    assert effect.sd_delta is not None
    assert size.delta == 0.125
    assert size.delta_source == "default"
    expected = -(-7.84 * (effect.sd_delta / 0.125) ** 2 // 1)
    assert size.recommended_pairs == int(expected)
    assert size.additional_pairs == max(0, int(expected) - 5)
    assert size.per_pair_cost_usd == pytest.approx(2.5)
    assert size.projected_cost_usd == pytest.approx(2.5 * int(expected))
    configured = Settings(
        outcome_types=OUTCOME_TYPES,
        min_pairs=5,
        bootstrap_samples=2000,
        min_effects={"solve_rate": 0.25},
        max_exclusion_difference=0.5,
    )
    resized = _effect(
        _run(paired_arms("solve_rate", baseline, treatment), 5, configured), "solve_rate"
    )
    assert resized.sample_size is not None
    assert resized.sample_size.delta_source == "configured"
    assert resized.sample_size.recommended_pairs < size.recommended_pairs
    assert report.sample_size_basis.standardized == {"1": 8, "0.5": 32, "0.25": 126}


def test_zero_spread_gives_no_projection_and_no_blanket_refusal() -> None:
    effect = _effect(_run(paired_arms("reached", [1.0] * 6, [2.0] * 6), 6), "reached")
    assert effect.verdict is Verdict.IMPROVED  # six identical one-way deltas: p = 0.03125
    assert effect.sample_size is not None
    assert effect.sample_size.recommended_pairs is None
    assert "SD is 0" in (effect.sample_size.reason or "")


def test_a_metric_with_no_better_direction_never_reads_improved_or_worse() -> None:
    neutral = dataclasses.replace(outcome_metric("solve_rate"), direction=Direction.NEUTRAL)
    deltas = [2.0, 2.5, 2.0, 1.5, 2.0, 1.0]
    summary = summarise(deltas, seed=1, bootstrap_samples=500)
    pairs = Exclusions(
        planned=6,
        paired=6,
        arms=(
            ArmExclusions(arm=BASELINE, planned=6, excluded=0),
            ArmExclusions(arm=GUIDED, planned=6, excluded=0),
        ),
    )

    verdict, _ = decide(
        neutral,
        summary,
        pairs,
        SETTINGS,
        sign_flip_test([Fraction(d) for d in deltas], SETTINGS.sign_flip_state_limit),
    )

    assert verdict is Verdict.INCONCLUSIVE


def test_the_baseline_arm_must_be_planned_with_a_treatment() -> None:
    bad = inputs(paired_arms("solve_rate", [0.0] * 5, [1.0] * 5), 5)
    with pytest.raises(ValueError, match="baseline arm"):
        analyse(dataclasses.replace(bad, baseline_arm="missing"), SETTINGS)
    with pytest.raises(ValueError, match="min_pairs"):
        Settings(min_pairs=1)


def _compliance(share: float) -> AssessResult:
    observations = tuple(
        Observation(metric=check, subject="checkpoint_1", status=AssessStatus.OK, value=share)
        for check in ("tests_run_before_finish", "plan_written")
    )
    return AssessResult(status=AssessStatus.OK, observations=observations)


def test_process_compliance_results_add_a_manipulation_check_per_comparison() -> None:
    outcomes = paired_arms("solve_rate", [0.2] * 5, [0.6] * 5)
    outcomes.append(trial(GUIDED, 5, {}, infra=True))
    process = {o.trial_id: _compliance(1.0 if o.arm == GUIDED else 0.0) for o in outcomes}
    base = inputs(outcomes, 6)
    report = analyse(dataclasses.replace(base, process_results=process), SETTINGS)
    manipulation = report.comparisons[0].manipulation
    assert manipulation is not None
    assert manipulation.verdict.value == "separated"
    # The infra-invalid guided trial is left out of the check.
    assert manipulation.checks[0].guided.trials == 5
    assert "### Manipulation check: **separated**" in render_markdown(report)
    assert analyse(base, SETTINGS).comparisons[0].manipulation is None


def test_lock_thresholds_merge_with_labelled_overrides() -> None:
    from raidar.evaluation.analysis.thresholds import settings_from_lock

    attempt = {
        "seed": 7,
        "min_pairs": 6,
        "max_exclusion_fraction": 0.1,
        "alpha": 0.01,
        "min_effects": {"solve_rate": 0.2, "headline_score": 5.0},
    }
    settings = settings_from_lock(
        attempt, overrides={"alpha": None, "min_pairs": 8, "min_effects": {"solve_rate": 0.3}}
    )
    assert (settings.pinned_by_lock, settings.seed, settings.alpha) == (True, 7, 0.01)
    assert settings.min_pairs == 8
    assert dict(settings.min_effects) == {"solve_rate": 0.3, "headline_score": 5.0}
    assert settings.overridden == ("min_pairs", "min_effect.solve_rate")
    assert settings.max_exclusion_difference is None
    pinned = settings_from_lock({**attempt, "max_exclusion_difference": 0.15})
    assert pinned.max_exclusion_difference == 0.15
    assert pinned.origin("max_exclusion_difference") == "pinned by the lock"
    overridden = settings_from_lock(attempt, overrides={"max_exclusion_difference": 0.4})
    assert overridden.max_exclusion_difference == 0.4
    assert overridden.origin("max_exclusion_difference") == "command-line override of the lock"
    assert settings.origin("min_pairs") == "command-line override of the lock"
    assert settings.origin("alpha") == "pinned by the lock"

    legacy = settings_from_lock({"seed": 3, "min_pairs": 9})
    assert (legacy.pinned_by_lock, legacy.min_pairs, legacy.alpha) == (False, 9, 0.05)
    assert legacy.source == "defaults (lock does not pin thresholds)"
    with pytest.raises(ValueError, match="unknown outcome metric"):
        settings_from_lock(attempt, overrides={"min_effects": {"bogus": 1.0}})


def test_the_pinned_alpha_sets_the_continuous_interval_level() -> None:
    from raidar.evaluation.analysis.stats import summarise

    deltas = [0.5, -0.1, 0.4, 0.3, 0.0, 0.2]
    verdicts = {}
    for alpha in (0.05, 0.20):
        summary = summarise(deltas, seed=1, bootstrap_samples=500, confidence=1 - alpha)
        assert summary.interval is not None
        assert summary.interval.confidence == pytest.approx(1 - alpha)
        verdicts[alpha] = summary.interval.contains_zero()
    assert verdicts == {0.05: True, 0.20: False}


def test_a_metric_undefined_for_a_problem_is_left_out_not_counted_ineligible() -> None:
    attempts = 6
    oneshot = [
        trial(arm, attempt, {"rework": gap("not_applicable", "K=1")}, problem="oneshot@1")
        for attempt in range(attempts)
        for arm in (BASELINE, GUIDED)
    ]
    multi = paired_arms(
        "rework", [0.4, 0.5, 0.3, 0.6, 0.5, 0.4], [0.2, 0.3, 0.2, 0.3, 0.2, 0.3], problem="multi@1"
    )
    report = _run(oneshot + multi, attempts, problems=("oneshot@1", "multi@1"))
    (comparison,) = report.comparisons
    rework = [effect for effect in comparison.effects if effect.metric == "rework"]
    assert [effect.problem for effect in rework] == ["multi@1"]
    assert rework[0].exclusions.planned == attempts
    assert rework[0].verdict is not Verdict.INSUFFICIENT


def test_unpinned_exclusion_difference_refuses_every_comparative_verdict() -> None:
    # Equal (zero) exclusion rates and a decisive effect: a non-significant difference alone
    # never establishes acceptable attrition, so without the pre-registered limit there is none.
    unpinned = Settings(min_pairs=5, bootstrap_samples=2000, outcome_types=OUTCOME_TYPES)
    outcomes = paired_arms("solve_rate", [0.0] * 8, [1.0] * 8)
    effect = _effect(_run(outcomes, 8, unpinned), "solve_rate")
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "no pre-registered material difference" in effect.verdict_reason
    assert _effect(_run(outcomes, 8), "solve_rate").verdict is Verdict.IMPROVED
    markdown = render_markdown(_run(outcomes, 8, unpinned))
    assert "unpinned (every comparative verdict refused)" in markdown


def test_uneven_attrition_between_arms_refuses_the_verdict_and_names_arm_and_cause() -> None:
    # Eleven attempts; the guided arm loses three trials to infrastructure, the baseline none.
    outcomes = paired_arms("solve_rate", [0.0] * 11, [1.0] * 11)
    for index in (13, 15, 17):
        outcomes[index] = trial(GUIDED, (index - 1) // 2, {"solve_rate": 1.0}, infra=True)
    strict = Settings(
        min_pairs=5,
        max_exclusion_fraction=0.5,
        max_exclusion_difference=0.2,
        outcome_types=OUTCOME_TYPES,
    )
    effect = _effect(_run(outcomes, 11, strict), "solve_rate")
    by_arm = {arm.arm: arm for arm in effect.exclusions.arms}
    assert by_arm[GUIDED].causes == {"infra_error:validity:steps_started": 3}
    assert by_arm[BASELINE].causes == {}
    assert (by_arm[GUIDED].planned, by_arm[BASELINE].planned) == (11, 11)
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "exclusion rates differ materially" in effect.verdict_reason
    assert "guided 27.3% (3/11)" in effect.verdict_reason
    lenient = Settings(
        min_pairs=5,
        max_exclusion_fraction=0.5,
        max_exclusion_difference=0.3,
        outcome_types=OUTCOME_TYPES,
    )
    assert _effect(_run(outcomes, 11, lenient), "solve_rate").verdict is Verdict.IMPROVED


@pytest.mark.parametrize(
    ("bound", "verdict"),
    [(None, Verdict.INSUFFICIENT), (0.1, Verdict.INSUFFICIENT), (0.2, Verdict.IMPROVED)],
)
def test_exclusion_difference_equality_passes_with_the_independent_paired_cap(
    bound: float | None, verdict: Verdict
) -> None:
    outcomes = paired_arms("solve_rate", [0.0] * 10, [1.0] * 10)
    for attempt in (0, 1):
        outcomes[2 * attempt + 1] = trial(GUIDED, attempt, {"solve_rate": 1.0}, infra=True)
    settings = dataclasses.replace(SETTINGS, max_exclusion_difference=bound)

    effect = _effect(_run(outcomes, 10, settings), "solve_rate")

    assert effect.verdict is verdict
    assert effect.exclusions.fraction == 0.2
    assert {arm.arm: arm.rate for arm in effect.exclusions.arms} == {
        BASELINE: 0.0,
        GUIDED: 0.2,
    }
    # A bound permitting this rate gap cannot override the independent paired cap.
    capped = dataclasses.replace(settings, max_exclusion_difference=1.0, max_exclusion_fraction=0.1)
    assert _effect(_run(outcomes, 10, capped), "solve_rate").verdict is Verdict.INSUFFICIENT


def test_exclusions_are_counted_per_arm_in_the_aggregate_too() -> None:
    outcomes = paired_arms("solve_rate", [0.0] * 8, [1.0] * 8, problem="a@1")
    outcomes += paired_arms("solve_rate", [0.0] * 8, [1.0] * 8, problem="b@1")
    outcomes[3] = trial(GUIDED, 1, {"solve_rate": 1.0}, problem="a@1", infra=True)
    outcomes[16 + 2] = trial(BASELINE, 1, {"solve_rate": gap("unavailable")}, problem="b@1")
    report = _run(outcomes, 8, problems=("a@1", "b@1"))
    aggregate = _effect(report, "solve_rate", "*")
    by_arm = {arm.arm: arm for arm in aggregate.exclusions.arms}
    assert by_arm[GUIDED].causes == {"infra_error:validity:steps_started": 1}
    assert by_arm[BASELINE].causes == {"ineligible:unavailable": 1}
    assert (by_arm[GUIDED].planned, by_arm[BASELINE].planned) == (16, 16)


def test_infra_exclusions_report_each_recorded_reason_per_arm() -> None:
    outcomes = paired_arms("solve_rate", [0.0] * 8, [1.0] * 8)
    outcomes[1] = outcomes[1].model_copy(
        update={
            "infra_error": True,
            "infra_reasons": ("validity:steps_started", "checkpoint_2:provider_failure"),
        }
    )
    outcomes[3] = outcomes[3].model_copy(update={"infra_error": True, "infra_reasons": ()})
    effect = _effect(_run(outcomes, 8), "solve_rate")
    guided = next(arm for arm in effect.exclusions.arms if arm.arm == GUIDED)
    assert guided.excluded == 2
    assert guided.causes == {
        "infra_error:checkpoint_2:provider_failure": 1,
        "infra_error:unrecorded": 1,
        "infra_error:validity:steps_started": 1,
    }
    assert "infra_error:checkpoint_2:provider_failure 1" in render_markdown(_run(outcomes, 8))


def test_a_lock_without_outcome_type_declarations_refuses_every_verdict() -> None:
    outcomes = paired_arms("solve_rate", [0.0] * 8, [1.0] * 8)
    undeclared = dataclasses.replace(SETTINGS, outcome_types=None)
    effect = _effect(_run(outcomes, 8, undeclared), "solve_rate")
    assert effect.verdict is Verdict.INSUFFICIENT
    assert effect.kind is None
    assert effect.test is None
    assert "declares no outcome types" in effect.verdict_reason
    assert "NOT declared: verdicts refused" in render_markdown(_run(outcomes, 8, undeclared))
    unlisted = dataclasses.replace(SETTINGS, outcome_types={"problems": {}, "aggregate": {}})
    assert "declares no outcome types for toy@1" in (
        _effect(_run(outcomes, 8, unlisted), "solve_rate").verdict_reason
    )
    no_aggregate = dataclasses.replace(SETTINGS, outcome_types={"problems": {}})
    many = paired_arms("solve_rate", [0.0] * 8, [1.0] * 8, problem="a@1")
    many += paired_arms("solve_rate", [0.0] * 8, [1.0] * 8, problem="b@1")
    aggregate = _effect(_run(many, 8, no_aggregate, problems=("a@1", "b@1")), "solve_rate", "*")
    assert aggregate.verdict is Verdict.INSUFFICIENT
    assert "declares no outcome types for *" in aggregate.verdict_reason


def test_a_single_checkpoint_problem_declares_its_rates_binary() -> None:
    # solve_rate_solvable over K = 1 has support {0, 1}; the lock declares that from the bound
    # checkpoint count, so the test is McNemar. Over K = 4 the same values would be sign-flip.
    one = dataclasses.replace(SETTINGS, outcome_types=declared_outcome_types({"toy@1": 1}))
    outcomes = paired_arms("solve_rate_solvable", [0.0] * 6, [1.0] * 6)
    binary = _effect(_run(outcomes, 6, one), "solve_rate_solvable")
    assert binary.kind is Kind.BINARY
    assert binary.mcnemar is not None
    assert binary.test is not None
    assert binary.test.name is ExactTestName.MCNEMAR
    assert binary.test.p_value == pytest.approx(0.03125)
    assert binary.verdict is Verdict.IMPROVED
    continuous = _effect(_run(outcomes, 6), "solve_rate_solvable")
    assert continuous.kind is Kind.CONTINUOUS
    assert continuous.test is not None
    assert continuous.test.name is ExactTestName.SIGN_FLIP
    assert continuous.mcnemar is None


def test_declared_outcome_types_follow_the_bound_checkpoint_count() -> None:
    declared = declared_outcome_types({"one@1": 1, "many@1": 4})
    one = set(declared["problems"]["one@1"]["binary"])
    many = set(declared["problems"]["many@1"]["binary"])
    assert {"final_solved", "solve_rate", "solve_rate_solvable", "reached"} <= one
    assert many == {"final_solved", "final_solved_errata", "final_solved_solvable"}
    assert "headline_score" not in one
    assert declared["aggregate"] == {"binary": []}
    assert declared_kind(outcome_metric("solve_rate"), 4) is Kind.CONTINUOUS


def test_the_exact_enumeration_limit_is_a_visible_resource_refusal_not_an_approximation() -> None:
    terms = [0.0] * 14
    treatment = [(index + 1) ** 0.5 for index in range(14)]
    outcomes = paired_arms("solve_rate", terms, treatment)
    effect = _effect(_run(outcomes, 14), "solve_rate")
    assert effect.test is not None
    assert effect.test.p_value == pytest.approx(2 / 2**14)  # computed under the default limit
    tight = dataclasses.replace(SETTINGS, sign_flip_state_limit=100)
    refused = _effect(_run(outcomes, 14, tight), "solve_rate")
    assert refused.verdict is Verdict.INSUFFICIENT
    assert refused.test is not None
    assert refused.test.p_value is None
    assert "execution safety limit" in (refused.test.note or "")
    assert "exact-state-limit" in refused.verdict_reason
    assert "safety limit 100 partial sums" in render_markdown(_run(outcomes, 14, tight))
    assert "safety limit 250000 partial sums" in render_markdown(_run(outcomes, 14))


def test_the_report_labels_development_pooling_replacement_and_pinned_set_provenance() -> None:
    pooling = PoolingReport(
        unmeasured=("old-1",),
        overrides_used=("subject", "timing_cost"),
        findings=(PoolFinding(field="subject", message="agent differs"),),
        timing_cost_comparable=False,
        capabilities={"isolation_proof": "full"},
        parity={"toy@1": ParityComparison(trials=2, record={"image_id": "img"})},
    )
    loaded = dataclasses.replace(
        inputs(paired_arms("solve_rate", [0.25] * 6, [0.5] * 6), 6),
        dev_job=DevRun(max_checkpoints=2, agent_override=None, note="truncated to 2"),
        replaced=("run-1/toy-baseline-b0",),
        solvable={"toy@1": {"sha256": "ab" * 32, "cases": {"checkpoint_1": 2}}},
        pooling=pooling,
    )

    report = analyse(loaded, SETTINGS)
    text = render_markdown(report)

    assert not report.citable
    assert report.dev_job is not None
    for fact in ("Not citable", "truncated to 2", "toy-baseline-b0", "checkpoint_1: 2 cases"):
        assert fact in text
    for fact in ("old-1", "agent differs", "not comparable", "full", "img"):
        assert fact in text
    assert report.solvable == {"toy@1": {"sha256": "ab" * 32, "cases": {"checkpoint_1": 2}}}

    varied = dataclasses.replace(
        loaded, pooling=PoolingReport(capabilities=None, varies=("harness",))
    )
    assert "harness" in render_markdown(analyse(varied, SETTINGS))


def test_the_report_shows_dependency_readings_errata_and_material_arm_differences() -> None:
    def affected(hits: int) -> tuple[DependencyStep, ...]:
        return tuple(
            DependencyStep(
                step=f"checkpoint_{index}",
                unprovided=[{"requirement": "x", "reason": "no wheel"}] if index < hits else [],
                undeclared_imports=None,
                solvable_solved=False if index < hits else None,
            )
            for index in range(4)
        )

    outcomes = [
        outcome.model_copy(
            update={
                "dependency_steps": affected(3 if outcome.arm == GUIDED else 0),
                "metrics": {**outcome.metrics, VERIFIER_DISAGREEMENTS: Value.ok(1.0)},
            }
        )
        for outcome in paired_arms("solve_rate", [0.25] * 6, [0.5] * 6)
    ]
    errata = {"toy@1": {"sha256": "cd" * 32, "exclude_from_solved": ["a", "b"]}}
    loaded = dataclasses.replace(inputs(outcomes, 6), errata=errata)

    report = analyse(loaded, SETTINGS)
    text = render_markdown(report)

    assert "Dependency readings" in text
    assert "75% (18/24)" in text
    assert "unmeasured" in text
    assert "18/18 judged" in text
    assert report.dependency_warnings
    assert "Problem errata" in text
    assert "2 exclude-from-solved" in text
    assert all(arm.verifier_diagnostics is not None for arm in report.arms)
