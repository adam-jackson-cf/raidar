"""Reporting preserves missing evidence, scope and cross-problem decisions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from raidar.evaluation.analysis.dependencies import ReadingShare
from raidar.evaluation.analysis.effects import Settings
from raidar.evaluation.analysis.markdown import _descriptive_rows, _share_cell, render_markdown
from raidar.evaluation.analysis.outcomes import Value
from raidar.evaluation.analysis.report import analyse
from raidar.evaluation.analysis.verification_effort import GateDescriptive
from raidar.scoring.catalog.outcomes import declared_outcome_types
from tests.analysis_support import inputs, paired_arms


@pytest.mark.parametrize("problems", [("a@1",), ("a@1", "b@1")])
def test_inapplicable_preregistered_primary_remains_insufficient(problems: tuple[str, ...]) -> None:
    outcomes = [
        outcome.model_copy(
            update={
                "metrics": {
                    **outcome.metrics,
                    "regression": Value.missing("not_applicable", "one checkpoint"),
                }
            }
        )
        for problem in problems
        for outcome in paired_arms("solve_rate", [0.0, 0.0], [1.0, 1.0], problem=problem)
    ]
    report = analyse(
        replace(inputs(outcomes, 2, problems), primary_outcome="regression"),
        Settings(bootstrap_samples=100),
    )
    primary = report.comparisons[0].primary
    assert primary.metric == "regression"
    assert primary.problem == ("*" if len(problems) > 1 else problems[0])
    assert primary.n_pairs == 0
    assert primary.mean_delta is None
    assert primary.verdict.value == "insufficient"


def test_binary_cross_problem_declaration_refuses_an_aggregate_verdict() -> None:
    problems = ("a@1", "b@1")
    outcomes = [
        outcome
        for problem in problems
        for outcome in paired_arms("solve_rate", [0.0] * 6, [1.0] * 6, problem=problem)
    ]
    declarations = declared_outcome_types(dict.fromkeys(problems, 4))
    declarations["aggregate"] = {"binary": ["solve_rate"]}
    report = analyse(
        replace(inputs(outcomes, 6, problems), primary_outcome="solve_rate"),
        Settings(
            min_pairs=2,
            bootstrap_samples=100,
            max_exclusion_difference=0.0,
            outcome_types=declarations,
        ),
    )
    primary = report.comparisons[0].primary
    assert primary.mean_delta == 1.0
    assert primary.verdict.value == "insufficient"
    assert "aggregate binary" in primary.verdict_reason


def test_gate_effects_weight_problems_equally_instead_of_pooling_trials() -> None:
    metric = "gate.mean_rounds_to_clean"
    outcomes = paired_arms(metric, [1.0, 1.0], [2.0, 2.0], problem="a@1")
    outcomes += paired_arms(metric, [1.0] * 4, [4.0] * 4, problem="b@1")
    report = analyse(
        replace(inputs(outcomes, 4, ("a@1", "b@1")), gate_results={}),
        Settings(bootstrap_samples=100),
    )
    effect = next(
        effect
        for effect in report.comparisons[0].gate_effects
        if effect.metric == metric and effect.problem == "*"
    )
    assert effect.mean_delta == 2.0
    assert effect.n_pairs == 2


def test_report_names_unmeasured_and_heuristic_dependency_readings() -> None:
    cell = _share_cell(
        ReadingShare(
            measured=4,
            affected=1,
            unmeasured=2,
            share=0.25,
            solvable_failed=1,
            solvable_judged=1,
            heuristic=1,
        )
    )
    assert "25% (1/4)" in cell
    assert "2 unmeasured" in cell
    assert "1 by name match only" in cell


def test_partial_arm_descriptives_show_a_gap_not_zero() -> None:
    rows = _descriptive_rows(
        [{"gate.unresolved_rate": GateDescriptive(n=1, mean=0.5, sd=None, interval=None)}, {}],
        ["gate.unresolved_rate"],
    )
    assert rows[0][-1] == "—"
    assert "0.5" in rows[0][-2]


def test_report_retains_rawless_primary_and_omits_unavailable_power_rows() -> None:
    report = analyse(
        replace(
            inputs(paired_arms("solve_rate", [0.0, 0.0], [1.0, 1.0]), 2),
            primary_outcome="solve_rate",
        ),
        Settings(bootstrap_samples=100),
    )
    comparison = report.comparisons[0]
    report = report.model_copy(
        update={
            "comparisons": (
                comparison.model_copy(
                    update={
                        "effects": tuple(
                            effect.model_copy(update={"sample_size": None})
                            for effect in comparison.effects
                        ),
                        "process_note": "The declared process scorer measured no paired trials",
                    }
                ),
            ),
            "solvable": {"toy@1": {"sha256": "a" * 64, "cases": None}},
        }
    )
    text = render_markdown(report)
    assert "**Primary outcome** `solve_rate`" in text
    assert "**Raw (beside the primary)**" not in text
    assert "The declared process scorer measured no paired trials" in text
    assert "no steps" in text


def test_verifier_sensitivity_notes_stay_beside_diagnostics_without_changing_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from raidar.design.problem.sensitivities import VerifierSensitivity
    from raidar.evaluation.analysis import report as report_module

    outcome_inputs = inputs(paired_arms("solve_rate", [0.0, 0.0], [1.0, 1.0]), 2)
    settings = Settings(bootstrap_samples=100)
    before = analyse(outcome_inputs, settings)
    note = VerifierSensitivity(
        case="test_deadline.py::test_timeout",
        checkpoint="checkpoint_1",
        kind="timing",
        spec_determined=True,
        spec="must terminate within the deadline",
        mechanism="wall clock sampling varies across grading runs",
        recorded="2026-11-01",
    )
    monkeypatch.setattr(report_module, "known_sensitivities", lambda problems: {"toy@1": (note,)})
    after = analyse(outcome_inputs, settings)
    assert after.verifier_sensitivities == {"toy@1": [note.model_dump(mode="json")]}
    assert after.comparisons == before.comparisons
