"""Manifest of ``test-coverage``: readings of how well the final mapped tests exercise criteria."""

from __future__ import annotations

from raidar.scoring.framework.manifest import (
    Direction,
    EvidenceInput,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)


def _metric(
    metric_id: str,
    description: str,
    metric_type: MetricType = MetricType.COUNT,
    unit: str | None = None,
    value_range: tuple[float, float] | None = None,
) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=description,
        level=MetricLevel.CHECKPOINT,
        type=metric_type,
        direction=Direction.NEUTRAL,
        tier=Tier.JEV,
        exactness=Exactness.NATIVE,
        unit=unit,
        range=value_range,
    )


TEST_COVERAGE = ScorerManifest(
    id="test-coverage",
    version="3",
    description=(
        "FXL process audit stage B as recorded readings: for each criterion of a "
        "checkpoint's sealed definition, Jev scores each of the builder's final mapped tests "
        "(source and one hop of helpers, from the retained test files) on three levels (never "
        "runs the situation, runs it but would still pass, would fail if the behaviour were "
        "wrong). A test with no real assertion, or marked skipped, is held to level 1 as FXL "
        "does. Per-criterion and per-test probabilities and every raw Jev call are kept in the "
        "Assessment's raw payloads; the observations only count and average them. No cut-off is "
        "applied. Not applicable without a define stage."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE,),
    metrics=(
        _metric("criteria", "Criteria of the sealed definition, regression ones included."),
        _metric("unmapped_criteria", "Criteria whose ac_tests entry names no test."),
        _metric("judged_criteria", "Criteria with at least one decided test score."),
        _metric(
            "undecided_criteria",
            "Criteria with mapped, readable tests whose every score stayed undecided.",
        ),
        _metric("unread_tests", "Mapped tests the retained test files do not hold."),
        _metric("undecided_questions", "Test scores Jev left undecided after retries."),
        _metric("jev_calls", "Jev requests made, every attempt counted."),
        _metric(
            "mean_best_expected_level",
            "Mean over judged criteria of the best expected level (P1 + 2 * P2, 0 to 2) among "
            "its mapped tests, a test without a real assertion or marked skipped held to 1.",
            MetricType.REAL,
            "level",
            (0.0, 2.0),
        ),
    ),
)
