"""Per-checkpoint curves with ``1 - alpha`` bands, per arm and as paired deltas (F10).

Bands are 95% at the default alpha.

Eight checkpoints are not eight samples: each point's band is computed across
trials (attempts) at that checkpoint, never across checkpoints. Only
execution-valid trials contribute; a checkpoint whose value is a
statused gap (for example not reached, or ineligible) leaves that point's
``n`` and is counted in ``missing``.

Two views are drawn for correctness (memo §10.1): ``solved`` (continue view,
each checkpoint as graded) and ``strict_solved`` (strict view, solved only
inside the reached prefix).
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.analysis.effects import IntervalOut, Settings, Slot, interval_out
from raidar.analysis.outcomes import (
    CHECKPOINT_SERIES,
    AnalysisModel,
    SeriesDef,
    TrialOutcome,
)
from raidar.analysis.stats import seed_for, summarise
from raidar.scorers.sdk.manifest import Direction


class Band(AnalysisModel):
    n: int
    missing: int
    mean: float | None
    ci: IntervalOut | None


class CurvePoint(AnalysisModel):
    index: int
    step: str
    baseline: Band
    treatment: Band
    delta: Band


class Curve(AnalysisModel):
    series: str
    label: str
    view: str
    direction: Direction
    problem: str
    points: tuple[CurvePoint, ...]


def _band(values: Sequence[float], missing: int, seed: int, settings: Settings) -> Band:
    summary = summarise(
        values,
        seed=seed,
        bootstrap_samples=settings.bootstrap_samples,
        confidence=1.0 - settings.alpha,
    )
    return Band(n=summary.n, missing=missing, mean=summary.mean, ci=interval_out(summary.interval))


def _arm_band(
    outcomes: Sequence[TrialOutcome], series: str, index: int, seed: int, settings: Settings
) -> Band:
    points = [outcome.point(series, index) for outcome in outcomes]
    values = [point.value for point in points if point.value is not None]
    return _band(values, len(points) - len(values), seed, settings)


def _delta_band(
    paired: Sequence[tuple[TrialOutcome, TrialOutcome]],
    series: str,
    index: int,
    seed: int,
    settings: Settings,
) -> Band:
    deltas: list[float] = []
    for baseline, treatment in paired:
        left, right = baseline.point(series, index), treatment.point(series, index)
        if left.value is not None and right.value is not None:
            deltas.append(right.value - left.value)
    return _band(deltas, len(paired) - len(deltas), seed, settings)


def _valid(outcomes: Sequence[TrialOutcome | None]) -> list[TrialOutcome]:
    return [outcome for outcome in outcomes if outcome is not None and not outcome.infra_error]


def series_curve(
    definition: SeriesDef,
    problem: str,
    slots: Sequence[Slot],
    settings: Settings,
    *,
    label: str,
) -> Curve:
    baseline = _valid([slot.baseline for slot in slots])
    treatment = _valid([slot.treatment for slot in slots])
    paired = [
        (slot.baseline, slot.treatment)
        for slot in slots
        if slot.baseline is not None
        and slot.treatment is not None
        and not (slot.baseline.infra_error or slot.treatment.infra_error)
    ]
    steps = next((o.steps for o in (*baseline, *treatment)), ())
    points = []
    for index, step in enumerate(steps):
        key = f"{label}:{problem}:{definition.id}:{index}"
        points.append(
            CurvePoint(
                index=index,
                step=step,
                baseline=_arm_band(
                    baseline, definition.id, index, seed_for(settings.seed, key + ":b"), settings
                ),
                treatment=_arm_band(
                    treatment, definition.id, index, seed_for(settings.seed, key + ":t"), settings
                ),
                delta=_delta_band(
                    paired, definition.id, index, seed_for(settings.seed, key + ":d"), settings
                ),
            )
        )
    return Curve(
        series=definition.id,
        label=definition.label,
        view=definition.view.value,
        direction=definition.direction,
        problem=problem,
        points=tuple(points),
    )


def problem_curves(
    problem: str, slots: Sequence[Slot], settings: Settings, *, label: str
) -> tuple[Curve, ...]:
    return tuple(
        series_curve(definition, problem, slots, settings, label=label)
        for definition in CHECKPOINT_SERIES
    )
