"""Trajectory values: rates over decided steps, totals over gated steps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.verification_effort.measured import Measured
from raidar.scoring.verification_effort.metrics import (
    CORRECTNESS_DELTA,
    FIX_DELTA,
    MEAN_ROUNDS,
    RATES,
    ROUNDS_TO_CLEAN,
    SUMMED,
)

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE

Values = Mapping[str, Measured]
Gated = Sequence[tuple[str, Values]]


def _rate(metric: str, steps: Sequence[Values]) -> Measured:
    decided = [values[metric].value for values in steps if values[metric].status is _OK]
    if not decided:
        return Measured.gap(_INSUFFICIENT, f"no gated step decided {metric}")
    numerator = float(sum(value or 0.0 for value in decided))
    return Measured(numerator / len(decided), numerator=numerator, denominator=float(len(decided)))


def _mean_rounds(steps: Sequence[Values]) -> Measured:
    clean = [
        values[ROUNDS_TO_CLEAN].value or 0.0
        for values in steps
        if values[ROUNDS_TO_CLEAN].status is _OK
    ]
    if not clean:
        return Measured.gap(_NA, "no gated step ended with the gate passing")
    return Measured(sum(clean) / len(clean))


def _total(metric: str, steps: Gated) -> Measured:
    applicable = [(name, values[metric]) for name, values in steps]
    applicable = [(name, item) for name, item in applicable if item.status is not _NA]
    gaps = [name for name, item in applicable if item.status is not _OK]
    if gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {', '.join(gaps)}")
    if not applicable:
        return Measured.gap(_NA, f"{metric} is not applicable at any gated step")
    return Measured(float(sum(item.value or 0.0 for _, item in applicable)))


def _fix_delta(steps: Sequence[Values]) -> Measured:
    """Mean correctness delta over the steps with a fix turn (unavailable if one lacks it)."""
    deltas = [values[CORRECTNESS_DELTA] for values in steps]
    gaps = [item for item in deltas if item.status in (_UNAVAILABLE, _INSUFFICIENT)]
    if gaps:
        reason = f"{CORRECTNESS_DELTA} is unmeasured at {len(gaps)} step(s)"
        return Measured.gap(_UNAVAILABLE, reason)
    values = [item.value for item in deltas if item.status is _OK]
    if not values:
        return Measured.gap(_NA, "no step had a fix turn to compare")
    total = float(sum(value for value in values if value is not None))
    return Measured(total / len(values), numerator=total, denominator=float(len(values)))


def trajectory_values(steps: Gated) -> dict[str, Measured]:
    """Every trajectory value over the gated steps (each step's name and values)."""
    measured = [values for _, values in steps]
    found = {rate: _rate(metric, measured) for rate, metric in RATES.items()}
    found[MEAN_ROUNDS] = _mean_rounds(measured)
    found |= {total: _total(metric, steps) for total, metric in SUMMED.items()}
    found[FIX_DELTA] = _fix_delta(measured)
    return found
