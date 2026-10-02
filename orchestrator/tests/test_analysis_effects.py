"""Analysis on synthetic trials with known effects (WS-J J1, J2).

Each scenario plants an effect and checks the verdict the report gives it:
improved, worse, inconclusive and insufficient, plus the exclusion counts,
cost beside the effect, the strict/continue views, curves and the J2
sample-size projection.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from raidar.analysis.effects import Settings, Verdict
from raidar.analysis.markdown import render_markdown
from raidar.analysis.outcomes import OUTCOME_METRICS
from raidar.analysis.report import AnalysisReport, analyse
from raidar.analysis.stats import IntervalMethod
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation
from tests.analysis_support import (
    BASELINE,
    GUIDED,
    gap,
    inputs,
    paired_arms,
    trial,
)

SETTINGS = Settings(min_pairs=5, max_exclusion_fraction=0.2, bootstrap_samples=2000, seed=7)


def _effect(report: AnalysisReport, metric: str, problem: str = "toy@1"):
    (comparison,) = report.comparisons
    return next(e for e in comparison.effects if e.metric == metric and e.problem == problem)


def _run(outcomes, attempts, settings=SETTINGS, problems=("toy@1",)) -> AnalysisReport:
    return analyse(inputs(outcomes, attempts, problems), settings)


def test_consistent_gain_is_improved_with_cost_beside() -> None:
    baseline = [0.25, 0.5, 0.25, 0.5, 0.375]
    treatment = [b + d for b, d in zip(baseline, [0.25, 0.375, 0.25, 0.5, 0.25], strict=True)]
    effect = _effect(_run(paired_arms("solve_rate", baseline, treatment), 5), "solve_rate")
    assert effect.verdict is Verdict.IMPROVED
    assert effect.n_pairs == 5
    assert effect.mean_delta == pytest.approx(0.325)
    assert effect.ci is not None
    assert effect.ci.method is IntervalMethod.PAIRED_T
    assert effect.ci.low > 0
    assert effect.cost.baseline_mean == pytest.approx(1.0)
    assert effect.cost.treatment_mean == pytest.approx(1.5)
    assert effect.cost.delta_mean == pytest.approx(0.5)
    assert [pair.attempt for pair in effect.pairs] == [0, 1, 2, 3, 4]


def test_consistent_loss_is_worse_and_direction_follows_the_metric() -> None:
    baseline = [4.0, 3.0, 4.0, 2.0, 3.0]
    treatment = [1.0, 1.0, 2.0, 0.0, 1.0]
    effect = _effect(_run(paired_arms("reached", baseline, treatment), 5), "reached")
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
    assert "spans 0" in effect.verdict_reason


def test_too_few_pairs_is_insufficient_even_with_a_large_effect() -> None:
    effect = _effect(
        _run(paired_arms("solve_rate", [0.0, 0.0, 0.0], [1.0, 0.9, 1.0]), 3), "solve_rate"
    )
    assert effect.verdict is Verdict.INSUFFICIENT
    assert effect.verdict_reason == "3 pairs < minimum 5 (not pinned by a lock)"


def test_excluded_share_above_the_limit_is_insufficient_and_counted_by_cause() -> None:
    outcomes = paired_arms("solve_rate", [0.2] * 7, [0.8, 0.9, 0.7, 0.8, 0.9, 0.8, 0.9])
    # Attempt 5: infra-invalid treatment; attempt 6: baseline value is an ineligible gap.
    outcomes[11] = trial(GUIDED, 5, {"solve_rate": 0.9}, infra=True)
    outcomes[12] = trial(BASELINE, 6, {"solve_rate": gap("unavailable", "no verdict")})
    # Attempt 7 is planned but never ran; attempt 8's baseline was never assessed.
    outcomes.append(trial(GUIDED, 7, {"solve_rate": 0.9}))
    outcomes += [trial(BASELINE, 8, {}), trial(GUIDED, 8, {"solve_rate": 0.9})]
    effect = _effect(_run(outcomes, 9), "solve_rate")
    exclusions = effect.exclusions
    assert (exclusions.planned, exclusions.paired) == (9, 5)
    assert (exclusions.infra_error, exclusions.not_run, exclusions.unassessed) == (1, 1, 1)
    assert exclusions.ineligible == 1
    assert exclusions.ineligible_statuses == {"unavailable": 1}
    assert effect.verdict is Verdict.INSUFFICIENT
    assert "4/9 planned pairs excluded" in effect.verdict_reason
    relaxed = Settings(min_pairs=5, max_exclusion_fraction=0.5, bootstrap_samples=2000)
    assert _effect(_run(outcomes, 9, relaxed), "solve_rate").verdict is Verdict.IMPROVED


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
    reseeded = Settings(min_pairs=5, bootstrap_samples=2000, seed=8)
    other = _effect(
        _run(paired_arms("solve_rate", baseline, treatment), 12, reseeded), "solve_rate"
    )
    assert other.ci != effect.ci


@pytest.mark.parametrize(
    ("progression", "primary"), [("continue", "solve_rate"), ("halt_on_failure", "reached")]
)
def test_primary_outcome_follows_progression_and_s_is_secondary(
    progression: str, primary: str
) -> None:
    outcomes = []
    for attempt in range(5):
        base = {"headline_score": 50.0, "solve_rate": 0.25, "reached": 1.0}
        wiggle = attempt % 2
        treated = {
            "headline_score": 60.0 + wiggle,
            "solve_rate": 0.75 - 0.125 * wiggle,
            "reached": 3.0 - wiggle,
        }
        outcomes += [trial(BASELINE, attempt, base), trial(GUIDED, attempt, treated)]
    report = analyse(inputs(outcomes, 5, progression=progression), SETTINGS)
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
    assert quality.exclusions.unassessed == 5
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
    configured = Settings(min_pairs=5, bootstrap_samples=2000, min_effects={"solve_rate": 0.25})
    resized = _effect(
        _run(paired_arms("solve_rate", baseline, treatment), 5, configured), "solve_rate"
    )
    assert resized.sample_size is not None
    assert resized.sample_size.delta_source == "configured"
    assert resized.sample_size.recommended_pairs < size.recommended_pairs
    assert report.sample_size_basis.standardized == {"1": 8, "0.5": 32, "0.25": 126}


def test_zero_spread_gives_no_projection() -> None:
    effect = _effect(_run(paired_arms("reached", [1.0] * 5, [2.0] * 5), 5), "reached")
    assert effect.verdict is Verdict.IMPROVED  # a constant shift: the CI collapses onto +1
    assert effect.sample_size is not None
    assert effect.sample_size.recommended_pairs is None
    assert "SD is 0" in (effect.sample_size.reason or "")


def test_report_is_json_and_markdown_ready() -> None:
    outcomes = paired_arms("headline_score", [50.0] * 5, [60.0, 61, 62, 59, 60])
    report = _run(outcomes, 5)
    document = json.loads(report.model_dump_json())
    assert document["kind"] == "raidar-analysis-report"
    assert document["arms"][0]["arm"] == BASELINE
    assert document["arms"][1]["cost_means"]["total_cost_usd"] == pytest.approx(1.5)
    text = render_markdown(report)
    assert "## guided vs baseline" in text
    assert "**improved**" in text
    assert "## Sample size (J2)" in text


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
    from raidar.analysis.thresholds import settings_from_lock

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
    assert settings.origin("min_pairs") == "command-line override of the lock"
    assert settings.origin("alpha") == "pinned by the lock"

    legacy = settings_from_lock({"seed": 3, "min_pairs": 9})
    assert (legacy.pinned_by_lock, legacy.min_pairs, legacy.alpha) == (False, 9, 0.05)
    assert legacy.source == "defaults (lock does not pin thresholds)"
    with pytest.raises(ValueError, match="unknown outcome metric"):
        settings_from_lock(attempt, overrides={"min_effects": {"bogus": 1.0}})


def test_the_pinned_alpha_sets_the_continuous_interval_level() -> None:
    from raidar.analysis.stats import summarise

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
