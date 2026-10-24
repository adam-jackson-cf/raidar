"""Descriptive statistics the Quality view needs beyond WS-J's (distributions, correlation).

Means, SDs, curve intervals, paired Δ intervals and verdicts are WS-J's
(``raidar.evaluation.analysis.stats``/``effects``), not here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from raidar.evaluation.analysis.stats import mean


def quantiles(values: Sequence[float]) -> tuple[float, float, float, float, float] | None:
    """Min, Q1, median, Q3, max with linear interpolation (``None`` when empty)."""
    if not values:
        return None
    ordered = sorted(values)

    def at(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower = math.floor(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return (ordered[0], at(0.25), at(0.5), at(0.75), ordered[-1])


def median(values: Sequence[float]) -> float:
    result = quantiles(values)
    if result is None:
        raise ValueError("median of no values")
    return result[2]


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Pearson r, or ``None`` when either side is constant."""
    left_mean, right_mean = mean(left), mean(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    if left_var == 0 or right_var == 0:
        return None
    return covariance / math.sqrt(left_var * right_var)
