"""Manifest of ``definition-coverage``: readings of how fully the definition captures the issue."""

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


DEFINITION_COVERAGE = ScorerManifest(
    id="definition-coverage",
    version="2",
    description=(
        "FXL process audit stage A as recorded readings: for each required issue unit of a "
        "checkpoint's define stage, Jev scores each criterion citing it (its statement, aspects "
        "and examples) on three levels (other behaviour, concerns it, states it). Per-unit and "
        "per-criterion level probabilities and every raw Jev call are kept in the Assessment's "
        "raw payloads; the observations only count and average them. No cut-off is applied: FXL's "
        "own review found its audit cut-offs uncalibrated. Not applicable without a define stage."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE,),
    metrics=(
        _metric("required_units", "Issue units the define stage marked required."),
        _metric("uncited_units", "Required units no criterion of the sealed definition cites."),
        _metric("judged_units", "Required units with at least one decided criterion score."),
        _metric(
            "undecided_units",
            "Required units cited by criteria whose every score stayed undecided.",
        ),
        _metric("undecided_questions", "Criterion scores Jev left undecided after retries."),
        _metric("jev_calls", "Jev requests made, every attempt counted."),
        _metric(
            "mean_best_expected_level",
            "Mean over judged units of the best expected level (P1 + 2 * P2, 0 to 2) among the "
            "criteria citing the unit.",
            MetricType.REAL,
            "level",
            (0.0, 2.0),
        ),
    ),
)
