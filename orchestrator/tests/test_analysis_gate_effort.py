"""Gate effort in analysis: per-arm descriptives, paired gate effects, ranking and the
pre-registered ``gate_clean_at_step_end`` manipulation check.

A baseline without the commit gate measures no gate effort (every value
``not_applicable``), so gate effects are paired only when both arms ran a gate;
otherwise the arms are described one by one and the comparison says why.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from raidar.analysis.assessed import GATE_EFFORT_REF, gate_metrics
from raidar.analysis.effects import Settings
from raidar.analysis.load import manipulation_requests
from raidar.analysis.manipulation import (
    GATE_CLEAN,
    CheckRequest,
    Separation,
    check_report,
    step_share,
)
from raidar.analysis.markdown import render_markdown
from raidar.analysis.outcomes import (
    GATE_METRICS,
    TrialOutcome,
    Value,
    gate_metrics_for,
    outcome_metric,
)
from raidar.analysis.report import AnalysisReport, analyse
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessResult, AssessStatus, Observation
from raidar.spec.loader import load_experiment
from raidar.spec.validation import SpecValidationError
from tests.analysis_support import BASELINE, GUIDED, inputs, trial
from tests.experiment_spec_support import experiment_document, write_experiment

SETTINGS = Settings(min_pairs=5, max_exclusion_fraction=0.2, bootstrap_samples=2000, seed=7)
NO_GATE = Value.missing("not_applicable", "no guardrail-gate@1 record")
FIRST_PASS = "gate.first_pass_clean_rate"
UNRESOLVED = "gate.unresolved_rate"


def _gated(first_pass: float, unresolved: float = 0.0) -> dict[str, float | Value]:
    metrics: dict[str, float | Value] = {metric.id: 1.0 for metric in GATE_METRICS}
    return metrics | {FIRST_PASS: first_pass, UNRESOLVED: unresolved, "solve_rate": 0.5}


def _ungated() -> dict[str, float | Value]:
    return {metric.id: NO_GATE for metric in GATE_METRICS} | {"solve_rate": 0.5}


def _outcomes(baseline_gated: bool) -> list[TrialOutcome]:
    outcomes = []
    for attempt in range(5):
        base = _gated(0.25) if baseline_gated else _ungated()
        outcomes.append(trial(BASELINE, attempt, base))
        outcomes.append(trial(GUIDED, attempt, _gated(0.75 + 0.05 * attempt, 0.1)))
    return outcomes


def _signal(values: list[float | None]) -> AssessResult:
    """A gate-effort result whose checkpoints carry ``gate_clean_at_step_end``."""
    observations = tuple(
        Observation(
            metric=GATE_CLEAN, subject=f"checkpoint_{index}", status=AssessStatus.OK, value=v
        )
        if v is not None
        else Observation(
            metric=GATE_CLEAN,
            subject=f"checkpoint_{index}",
            status=AssessStatus.NOT_APPLICABLE,
            reason="no gate",
        )
        for index, v in enumerate(values, 1)
    )
    measured = any(item.status is AssessStatus.OK for item in observations)
    return AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.NOT_APPLICABLE,
        reason=None if measured else "no gate",
        observations=observations,
    )


def _analyse(outcomes: list[TrialOutcome], **changes: object) -> AnalysisReport:
    gate = {outcome.trial_id: None for outcome in outcomes}
    base = dataclasses.replace(inputs(outcomes, 5), gate_results=gate)
    return analyse(dataclasses.replace(base, **changes), SETTINGS)


def test_a_baseline_without_the_gate_is_described_not_paired() -> None:
    report = _analyse(_outcomes(baseline_gated=False))

    baseline, guided = report.arms
    assert baseline.gate_effort is not None
    assert guided.gate_effort is not None
    assert baseline.gate_effort[FIRST_PASS].n == 0
    assert baseline.gate_effort[FIRST_PASS].gaps == {"not_applicable": 5}
    assert guided.gate_effort[FIRST_PASS].n == 5
    assert guided.gate_effort[FIRST_PASS].mean == pytest.approx(0.85)
    assert guided.gate_effort[FIRST_PASS].interval is not None
    (comparison,) = report.comparisons
    assert comparison.gate_effects == ()
    assert comparison.gate_note is not None
    assert "baseline arm measured no gate-effort value" in comparison.gate_note
    markdown = render_markdown(report)
    assert "## Gate effort (gate-effort@1, per arm)" in markdown
    assert "— (not_applicable 5)" in markdown
    assert "### Gate effort (paired)" in markdown


def test_two_gated_arms_get_paired_gate_effects_with_verdicts() -> None:
    report = _analyse(_outcomes(baseline_gated=True))

    (comparison,) = report.comparisons
    effects = {effect.metric: effect for effect in comparison.gate_effects}
    assert comparison.gate_note is None
    # The lock declares gate-effort@1 (the default label): exactly the outcomes it reports.
    assert set(effects) == {metric.id for metric in gate_metrics_for("gate-effort@1")}
    first_pass = effects[FIRST_PASS]
    assert (first_pass.n_pairs, first_pass.verdict.value) == (5, "improved")
    assert effects[UNRESOLVED].verdict.value == "worse"
    assert "| gate.first_pass_clean_rate | ↑ higher | gate |" in render_markdown(report)


def test_without_gate_effort_declared_the_report_has_no_gate_section() -> None:
    report = analyse(inputs(_outcomes(baseline_gated=True), 5), SETTINGS)

    assert all(arm.gate_effort is None for arm in report.arms)
    assert report.comparisons[0].gate_effects == ()
    assert "Gate effort" not in render_markdown(report)


def test_the_ranking_view_carries_each_arms_gate_means() -> None:
    report = _analyse(_outcomes(baseline_gated=False), kind="benchmark")

    assert report.ranking is not None
    rows = {row.arm: row for row in report.ranking.rows}
    assert rows[GUIDED].gate_means is not None
    assert rows[GUIDED].gate_means[FIRST_PASS] == pytest.approx(0.85)
    assert rows[BASELINE].gate_means is not None
    assert rows[BASELINE].gate_means[FIRST_PASS] is None
    assert "gate.first_pass_clean_rate" in render_markdown(report)
    assert outcome_metric(FIRST_PASS).view.value == "gate"


def test_gate_metrics_read_the_trajectory_observations() -> None:
    result = AssessResult(
        status=AssessStatus.OK,
        observations=(
            Observation(
                metric="first_pass_clean_rate",
                subject=TRAJECTORY_SUBJECT,
                status=AssessStatus.OK,
                value=0.5,
                numerator=1,
                denominator=2,
            ),
        ),
    )
    metrics = gate_metrics(result)
    assert metrics[FIRST_PASS] == Value.ok(0.5)
    assert metrics[UNRESOLVED].status == "unavailable"
    assert gate_metrics(None)[FIRST_PASS].status == "unassessed"
    assert GATE_EFFORT_REF == "gate-effort@1"


# ---- the pre-registered manipulation check ------------------------------------------------


def _report(
    guided: list[AssessResult], baseline: list[AssessResult], min_share: float | None = 0.8
) -> Separation:
    report = check_report(
        {GATE_CLEAN: {GUIDED: guided, BASELINE: baseline}},
        [CheckRequest(check=GATE_CLEAN, min_share=min_share)],
        guided=GUIDED,
        baseline=BASELINE,
        preregistered=True,
    )
    assert report.preregistered
    return report.checks[0].verdict


def test_the_gate_check_against_an_ungated_baseline_is_a_pre_registered_threshold() -> None:
    ungated = [_signal([None, None]) for _ in range(5)]
    clean = [_signal([1.0, 1.0]) for _ in range(4)] + [_signal([1.0, 0.0])]

    report = check_report(
        {GATE_CLEAN: {GUIDED: clean, BASELINE: ungated}},
        [CheckRequest(check=GATE_CLEAN, min_share=0.8)],
        guided=GUIDED,
        baseline=BASELINE,
    )

    (check,) = report.checks
    assert (check.basis, check.verdict) == ("threshold", Separation.SEPARATED)
    assert check.guided_steps is not None
    assert (check.guided_steps.passed, check.guided_steps.decided) == (9, 10)
    assert "the condition is absent there" in check.reason
    low = [_signal([0.0, 1.0]) for _ in range(5)]
    assert _report(low, ungated) is Separation.NOT_SEPARATED
    assert _report([], ungated) is Separation.INSUFFICIENT_DATA
    assert step_share([], GATE_CLEAN).share is None


def test_two_gated_arms_need_the_threshold_and_separation() -> None:
    guided = [_signal([1.0, 1.0]) for _ in range(5)]
    baseline = [_signal([0.0, 0.0]) for _ in range(5)]
    assert _report(guided, baseline) is Separation.SEPARATED
    assert _report(guided, guided) is Separation.NOT_SEPARATED
    assert _report(baseline, guided) is Separation.NOT_SEPARATED
    assert _report(guided, baseline, min_share=None) is Separation.SEPARATED


def test_the_report_runs_the_locks_checks_from_their_own_scorer() -> None:
    outcomes = _outcomes(baseline_gated=False)
    gate = {
        outcome.trial_id: _signal([1.0, 1.0] if outcome.arm == GUIDED else [None, None])
        for outcome in outcomes
    }
    report = analyse(
        dataclasses.replace(
            inputs(outcomes, 5),
            gate_results=gate,
            manipulation_checks=(CheckRequest(check=GATE_CLEAN, min_share=0.8),),
        ),
        SETTINGS,
    )

    manipulation = report.comparisons[0].manipulation
    assert manipulation is not None
    assert manipulation.preregistered
    assert manipulation.verdict is Separation.SEPARATED
    assert "pre-registered by the lock" in render_markdown(report)
    assert "threshold (min share 0.8)" in render_markdown(report)


def test_lock_manipulation_checks_are_read_and_validated(tmp_path: Path) -> None:
    assert manipulation_requests({}) is None
    (request,) = (
        manipulation_requests({"manipulation_checks": [{"check": GATE_CLEAN, "min_share": 0.8}]})
        or ()
    )
    assert (request.check, request.min_share) == (GATE_CLEAN, 0.8)

    document = experiment_document()
    document["analysis"]["manipulation_checks"] = [{"check": GATE_CLEAN, "min_share": 0.8}]
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(write_experiment(tmp_path / "undeclared", document))
    issues = {issue.loc: issue.message for issue in caught.value.issues}
    assert "declare it in assessment.scorers" in issues["analysis.manipulation_checks.0.check"]

    document["analysis"]["manipulation_checks"] = [{"check": "vibes"}]
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(write_experiment(tmp_path / "unknown", document))
    assert "unknown check 'vibes'" in caught.value.issues[0].message

    document["assessment"]["scorers"] = ["correctness@1", "gate-effort@1"]
    document["analysis"]["manipulation_checks"] = [{"check": GATE_CLEAN, "min_share": 0.8}]
    document["analysis"]["min_effects"] = {FIRST_PASS: 0.2}
    spec = load_experiment(write_experiment(tmp_path / "ok", document)).spec
    assert spec.to_json()["analysis"]["manipulation_checks"] == [
        {"check": GATE_CLEAN, "min_share": 0.8}
    ]
    assert (
        "manipulation_checks"
        not in load_experiment(
            write_experiment(tmp_path / "plain", experiment_document())
        ).spec.to_json()["analysis"]
    )

    document["analysis"]["manipulation_checks"] = [{"check": GATE_CLEAN}, {"check": GATE_CLEAN}]
    with pytest.raises(SpecValidationError, match="repeats"):
        load_experiment(write_experiment(tmp_path / "twice", document))


# ---- gate-effort@4 and the process outcomes ---------------------------------------------

DELTA = "gate.fix_round_correctness_delta"
STALL = "process.stall_rate"


def _iteration_three(attempt: int, *, shadow: bool) -> dict[str, float | Value]:
    metrics = _gated(0.0) | {STALL: 0.25 if shadow else 0.125 * (attempt % 2)}
    if shadow:
        metrics[DELTA] = Value.missing("not_applicable", "shadow gate")
    else:
        metrics[DELTA] = -0.05 * attempt
    return metrics


def test_a_v4_lock_reports_the_fix_round_delta_and_the_process_stall_rate() -> None:
    outcomes = [
        item
        for attempt in range(5)
        for item in (
            trial(BASELINE, attempt, _iteration_three(attempt, shadow=True)),
            trial(GUIDED, attempt, _iteration_three(attempt, shadow=False)),
        )
    ]

    report = _analyse(outcomes, gate_scorer="gate-effort@4", process_scorer="process-compliance@3")

    baseline, guided = report.arms
    assert baseline.gate_effort is not None
    assert guided.gate_effort is not None
    assert baseline.gate_effort[DELTA].gaps == {"not_applicable": 5}
    assert guided.gate_effort[DELTA].mean == pytest.approx(-0.1)
    assert set(guided.gate_effort) == {metric.id for metric in gate_metrics_for("gate-effort@4")}
    (comparison,) = report.comparisons
    assert DELTA not in {effect.metric for effect in comparison.gate_effects}, "r1 has none"
    (stall,) = comparison.process_effects
    assert (stall.metric, stall.n_pairs) == (STALL, 5)
    assert baseline.process_outcomes is not None
    assert baseline.process_outcomes[STALL].mean == pytest.approx(0.25)
    markdown = render_markdown(report)
    assert "## Process outcomes (process-compliance@3, per arm)" in markdown
    assert "### Process outcomes (paired)" in markdown
    assert f"| {DELTA} |" in markdown


def test_a_lock_without_process_compliance_v3_has_no_process_section() -> None:
    report = _analyse(_outcomes(baseline_gated=True), process_scorer="process-compliance@2")

    assert all(arm.process_outcomes is None for arm in report.arms)
    assert report.comparisons[0].process_effects == ()
    assert "Process outcomes" not in render_markdown(report)


def test_a_trial_reads_only_the_outcomes_its_scorer_versions_report() -> None:
    from raidar.analysis.assessed import process_metrics

    stall = Observation(
        metric="stall_rate", subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=0.5
    )
    result = AssessResult(status=AssessStatus.OK, observations=(stall,))

    assert DELTA not in gate_metrics(None, "gate-effort@3")
    assert DELTA in gate_metrics(None, "gate-effort@4")
    assert process_metrics(result) == {STALL: Value.ok(0.5)}
    assert process_metrics(None)[STALL].status == "unassessed"
