"""Refuse incoherent observations and invalid statistical decision settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from raidar.evaluation.analysis.effects import Exclusions, Settings, exclusion_refusal
from raidar.evaluation.analysis.outcomes import Value
from raidar.evaluation.analysis.stats import (
    confidence_label,
    mcnemar_exact,
    pairs_for_power,
    regularized_beta,
    t_cdf,
    t_quantile,
)
from raidar.evaluation.analysis.thresholds import settings_from_lock
from raidar.evaluation.review.quality.models import Cell, cell_status
from raidar.evaluation.review.quality.stats import median


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ok"},
        {"status": "unavailable", "value": 0.0, "reason": "not measured"},
        {"status": "unavailable"},
        {"status": "unavailable", "reason": ""},
    ],
)
@pytest.mark.parametrize("model", [Value, Cell])
def test_statused_observations_refuse_missing_values_and_fabricated_zeroes(model, payload) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_review_refuses_unknown_assessment_status() -> None:
    with pytest.raises(ValueError, match="unknown assessment status"):
        cell_status("pending")


@pytest.mark.parametrize(
    "overrides",
    [
        {"sign_flip_state_limit": 0},
        {"bootstrap_samples": 99},
        {"max_exclusion_fraction": -0.01},
        {"max_exclusion_fraction": 1.01},
        {"max_exclusion_difference": -0.01},
        {"max_exclusion_difference": 1.01},
        {"alpha": 0.0},
        {"alpha": 1.0},
        {"min_effects": {"solve_rate": 0.0}},
        {"min_effects": {"solve_rate": -0.1}},
    ],
)
def test_verdict_settings_refuse_impossible_counts_fractions_and_effects(overrides) -> None:
    key = next(iter(overrides))
    reason = "effects of interest" if key == "min_effects" else key
    with pytest.raises(ValueError, match=reason):
        Settings(**overrides)


def test_absent_arm_exclusion_readings_cannot_authorize_a_comparison() -> None:
    exclusions = Exclusions(planned=6, paired=6)
    refusal = exclusion_refusal(exclusions, Settings(max_exclusion_difference=0.1))
    assert refusal is not None
    assert "per-arm exclusion rates are unavailable" in refusal


def test_nonmapping_legacy_effects_do_not_invent_an_effect_of_interest() -> None:
    settings = settings_from_lock({"min_effects": None})
    assert settings.min_effects == {}
    assert not settings.pinned_by_lock


@pytest.mark.parametrize(("x", "expected"), [(0.0, 0.0), (1.0, 1.0)])
def test_beta_distribution_includes_its_support_endpoints(x: float, expected: float) -> None:
    assert regularized_beta(2.0, 3.0, x) == expected


@pytest.mark.parametrize("df", [0.0, -1.0])
def test_student_distribution_refuses_nonpositive_degrees_of_freedom(df: float) -> None:
    with pytest.raises(ValueError, match="degrees of freedom"):
        t_cdf(1.0, df)


@pytest.mark.parametrize("probability", [0.0, 0.5, 1.0])
def test_upper_student_quantile_refuses_probabilities_outside_its_domain(
    probability: float,
) -> None:
    with pytest.raises(ValueError, match="probability"):
        t_quantile(probability, 5)


@pytest.mark.parametrize(("left", "right"), [(-1, 0), (0, -1)])
def test_mcnemar_refuses_negative_discordant_counts(left: int, right: int) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        mcnemar_exact(left, right)


def test_power_projection_refuses_negative_spread() -> None:
    with pytest.raises(ValueError, match="SD must be non-negative"):
        pairs_for_power(-1.0, 0.5)


@pytest.mark.parametrize(("confidence", "label"), [(0.95, "95%"), (0.9, "90%"), (0.975, "97.5%")])
def test_report_confidence_levels_preserve_fractional_percentages(
    confidence: float, label: str
) -> None:
    assert confidence_label(confidence) == label


def test_median_cannot_turn_missing_observations_into_zero() -> None:
    with pytest.raises(ValueError, match="median of no values"):
        median([])
