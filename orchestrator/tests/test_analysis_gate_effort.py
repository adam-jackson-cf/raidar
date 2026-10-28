"""Gate effort in analysis: per-arm descriptives, paired gate effects, ranking and the
default ``gate_clean_at_step_end`` manipulation check.

A baseline without the commit gate measures no gate effort (every value
``not_applicable``), so gate effects are paired only when both arms ran a gate;
otherwise the arms are described one by one and the comparison says why.
"""

from __future__ import annotations

import dataclasses

import pytest

from raidar.evaluation.analysis.assessed import GATE_EFFORT_REF, gate_metrics
from raidar.evaluation.analysis.effects import Settings
from raidar.evaluation.analysis.manipulation import Separation, check_report
from raidar.evaluation.analysis.markdown import render_markdown
from raidar.evaluation.analysis.outcomes import TrialOutcome, Value
from raidar.evaluation.analysis.report import AnalysisReport, analyse
from raidar.scoring.catalog.checks import GATE_CLEAN
from raidar.scoring.catalog.outcomes import GATE_METRICS, outcome_metric
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)
from tests.analysis_support import BASELINE, GUIDED, inputs, trial

SETTINGS = Settings(min_pairs=5, max_exclusion_fraction=0.2, bootstrap_samples=2000, seed=7)
NO_GATE = Value.missing("not_applicable", "no guardrail-gate record")
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
    assert "## Gate effort (gate-effort@14, per arm)" in markdown
    assert "— (not_applicable 5)" in markdown
    assert "### Gate effort (paired)" in markdown


def test_two_gated_arms_get_paired_gate_effects_with_verdicts() -> None:
    report = _analyse(_outcomes(baseline_gated=True))

    (comparison,) = report.comparisons
    effects = {effect.metric: effect for effect in comparison.gate_effects}
    assert comparison.gate_note is None
    # The lock declares gate-effort (the default label): exactly the outcomes it reports.
    assert set(effects) == {metric.id for metric in GATE_METRICS}
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
    assert GATE_EFFORT_REF == "gate-effort@14"


# ---- the default manipulation check -------------------------------------------------------


def _report(guided: list[AssessResult], baseline: list[AssessResult]) -> Separation:
    report = check_report(
        {GATE_CLEAN: {GUIDED: guided, BASELINE: baseline}},
        [GATE_CLEAN],
        guided=GUIDED,
        baseline=BASELINE,
    )
    return report.checks[0].verdict


def test_the_gate_check_against_an_ungated_baseline_has_no_data_to_separate() -> None:
    ungated = [_signal([None, None]) for _ in range(5)]
    clean = [_signal([1.0, 1.0]) for _ in range(5)]

    (check,) = check_report(
        {GATE_CLEAN: {GUIDED: clean, BASELINE: ungated}},
        [GATE_CLEAN],
        guided=GUIDED,
        baseline=BASELINE,
    ).checks

    assert check.verdict is Separation.INSUFFICIENT_DATA
    assert (check.guided.trials, check.baseline.trials) == (5, 0)
    assert _report([], ungated) is Separation.INSUFFICIENT_DATA


def test_two_gated_arms_need_separation() -> None:
    guided = [_signal([1.0, 1.0]) for _ in range(5)]
    baseline = [_signal([0.0, 0.0]) for _ in range(5)]
    assert _report(guided, baseline) is Separation.SEPARATED
    assert _report(guided, guided) is Separation.NOT_SEPARATED
    assert _report(baseline, guided) is Separation.NOT_SEPARATED


def test_the_report_runs_the_gate_check_from_the_gate_effort_scorer() -> None:
    outcomes = _outcomes(baseline_gated=True)
    gate = {
        outcome.trial_id: _signal([1.0, 1.0] if outcome.arm == GUIDED else [0.0, 0.0])
        for outcome in outcomes
    }
    report = analyse(dataclasses.replace(inputs(outcomes, 5), gate_results=gate), SETTINGS)

    manipulation = report.comparisons[0].manipulation
    assert manipulation is not None
    assert [check.check for check in manipulation.checks] == [GATE_CLEAN]
    assert manipulation.verdict is Separation.SEPARATED
    assert GATE_CLEAN in render_markdown(report)


# ---- the process outcomes ---------------------------------------------

DELTA = "gate.fix_round_correctness_delta"
STALL = "process.stall_rate"


def _iteration_three(attempt: int, *, shadow: bool) -> dict[str, float | Value]:
    metrics = _gated(0.0) | {STALL: 0.25 if shadow else 0.125 * (attempt % 2)}
    if shadow:
        metrics[DELTA] = Value.missing("not_applicable", "shadow gate")
    else:
        metrics[DELTA] = -0.05 * attempt
    return metrics


def test_a_lock_reports_the_fix_round_delta_and_the_process_stall_rate() -> None:
    outcomes = [
        item
        for attempt in range(5)
        for item in (
            trial(BASELINE, attempt, _iteration_three(attempt, shadow=True)),
            trial(GUIDED, attempt, _iteration_three(attempt, shadow=False)),
        )
    ]

    report = _analyse(
        outcomes, gate_scorer="gate-effort@14", process_scorer="process-compliance@10"
    )

    baseline, guided = report.arms
    assert baseline.gate_effort is not None
    assert guided.gate_effort is not None
    assert baseline.gate_effort[DELTA].gaps == {"not_applicable": 5}
    assert guided.gate_effort[DELTA].mean == pytest.approx(-0.1)
    assert set(guided.gate_effort) == {metric.id for metric in GATE_METRICS}
    (comparison,) = report.comparisons
    assert DELTA not in {effect.metric for effect in comparison.gate_effects}, "r1 has none"
    (stall,) = comparison.process_effects
    assert (stall.metric, stall.n_pairs) == (STALL, 5)
    assert baseline.process_outcomes is not None
    assert baseline.process_outcomes[STALL].mean == pytest.approx(0.25)
    markdown = render_markdown(report)
    assert "## Process outcomes (process-compliance@10, per arm)" in markdown
    assert "### Process outcomes (paired)" in markdown
    assert f"| {DELTA} |" in markdown


def test_a_lock_without_process_compliance_has_no_process_section() -> None:
    report = _analyse(_outcomes(baseline_gated=True), process_scorer=None)

    assert all(arm.process_outcomes is None for arm in report.arms)
    assert report.comparisons[0].process_effects == ()
    assert "Process outcomes" not in render_markdown(report)


def test_a_trial_reads_the_process_outcomes_its_scorer_reports() -> None:
    from raidar.evaluation.analysis.assessed import process_metrics

    stall = Observation(
        metric="stall_rate", subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=0.5
    )
    result = AssessResult(status=AssessStatus.OK, observations=(stall,))

    assert DELTA in gate_metrics(None)
    assert process_metrics(result)[STALL] == Value.ok(0.5)
    assert process_metrics(None)[STALL].status == "unassessed"
