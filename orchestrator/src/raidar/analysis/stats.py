"""Paired statistics for arm comparisons (F10, design memo §2.3).

Everything here is deterministic and stdlib-only:

- a 95% interval for a mean: paired t below ``BOOTSTRAP_MIN_PAIRS`` pairs, a
  percentile bootstrap over whole pairs from that size on, drawn from a
  ``random.Random`` seeded per estimate so a report replays byte-identically;
- the exact two-sided McNemar test for a paired binary outcome;
- the pairs needed for 80% power at alpha = 0.05, ``n = 7.84·(SD/δ)²``.

Student-t quantiles come from the regularized incomplete beta function
(continued fraction, as in Numerical Recipes ``betacf``) inverted by bisection.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

CONFIDENCE = 0.95
BOOTSTRAP_MIN_PAIRS = 10
BOOTSTRAP_SAMPLES = 10_000
POWER_CONSTANT = 7.84  # (z_{0.975} + z_{0.80})^2: 80% power, two-sided alpha 0.05
_BETA_ITERATIONS = 300
_BETA_EPSILON = 3e-16
_TINY = 1e-300
_BISECTION_STEPS = 200


class IntervalMethod(StrEnum):
    PAIRED_T = "paired_t"
    BOOTSTRAP = "bootstrap_percentile"
    PROBLEM_EQUAL_WEIGHT_T = "problem_equal_weight_t"


@dataclass(frozen=True)
class Interval:
    low: float
    high: float
    method: IntervalMethod
    confidence: float = CONFIDENCE

    def contains_zero(self) -> bool:
        return self.low <= 0.0 <= self.high


@dataclass(frozen=True)
class Summary:
    """Mean, SD and interval of a sample (SD and interval need two values)."""

    n: int
    mean: float | None
    sd: float | None
    interval: Interval | None


def _beta_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    result = d
    for m in range(1, _BETA_ITERATIONS + 1):
        m2 = 2 * m
        for numerator in (
            m * (b - m) * x / ((qam + m2) * (a + m2)),
            -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > _TINY else _TINY)
            c = 1.0 + numerator / c
            c = c if abs(c) > _TINY else _TINY
            result *= d * c
        if abs(d * c - 1.0) < _BETA_EPSILON:
            break
    return result


def regularized_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b) for a, b > 0 and x in [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Student-t cumulative distribution function."""
    if df <= 0:
        raise ValueError(f"degrees of freedom must be positive, got {df}")
    tail = 0.5 * regularized_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def t_quantile(probability: float, df: float) -> float:
    """Inverse Student-t CDF for a probability in (0.5, 1), by bisection."""
    if not 0.5 < probability < 1.0:
        raise ValueError(f"probability must be in (0.5, 1), got {probability}")
    low, high = 0.0, 1.0
    while t_cdf(high, df) < probability:
        high *= 2.0
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2.0
        if t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def sample_sd(values: Sequence[float]) -> float:
    centre = mean(values)
    return math.sqrt(math.fsum((value - centre) ** 2 for value in values) / (len(values) - 1))


def seed_for(base_seed: int, label: str) -> int:
    """A stable per-estimate seed, so each interval replays whatever else is computed."""
    digest = hashlib.sha256(f"{base_seed}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def t_interval(
    centre: float,
    standard_error: float,
    df: float,
    method: IntervalMethod,
    *,
    confidence: float = CONFIDENCE,
) -> Interval:
    half = t_quantile(1.0 - (1.0 - confidence) / 2.0, df) * standard_error
    return Interval(low=centre - half, high=centre + half, method=method, confidence=confidence)


def bootstrap_interval(
    values: Sequence[float], *, seed: int, samples: int, confidence: float = CONFIDENCE
) -> Interval:
    """Percentile bootstrap of the mean, resampling whole pairs."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(math.fsum(rng.choices(values, k=n)) / n for _ in range(samples))
    alpha = (1.0 - confidence) / 2.0
    low = means[max(0, math.floor(alpha * samples))]
    high = means[min(samples - 1, math.ceil((1.0 - alpha) * samples) - 1)]
    return Interval(low=low, high=high, method=IntervalMethod.BOOTSTRAP, confidence=confidence)


def confidence_label(confidence: float) -> str:
    """``95%``, ``90%``, ``97.5%``: the interval's level as reports print it."""
    return f"{round(confidence * 100, 4):g}%"


def summarise(
    values: Sequence[float],
    *,
    seed: int,
    bootstrap_samples: int,
    confidence: float = CONFIDENCE,
) -> Summary:
    """Mean, SD and interval at ``confidence`` (the pinned 1 - alpha in analysis):
    paired t below 10 values, bootstrap from 10 on."""
    n = len(values)
    if n == 0:
        return Summary(n=0, mean=None, sd=None, interval=None)
    centre = mean(values)
    if n < 2:
        return Summary(n=n, mean=centre, sd=None, interval=None)
    sd = sample_sd(values)
    if n >= BOOTSTRAP_MIN_PAIRS:
        interval = bootstrap_interval(
            values, seed=seed, samples=bootstrap_samples, confidence=confidence
        )
    else:
        interval = t_interval(
            centre, sd / math.sqrt(n), n - 1, IntervalMethod.PAIRED_T, confidence=confidence
        )
    return Summary(n=n, mean=centre, sd=sd, interval=interval)


def mcnemar_exact(baseline_only: int, treatment_only: int) -> float:
    """Exact two-sided McNemar p-value from the discordant pair counts."""
    if baseline_only < 0 or treatment_only < 0:
        raise ValueError("discordant counts must be non-negative")
    discordant = baseline_only + treatment_only
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(baseline_only, treatment_only) + 1))
    return min(1.0, 2.0 * tail / 2.0**discordant)


def pairs_for_power(sd: float, delta: float) -> int:
    """Pairs for 80% power at two-sided alpha 0.05: ``ceil(7.84·(SD/δ)²)``.

    Rounded to 9 places before the ceiling so float noise in an exact product
    (7.84·6.25 = 49) never adds a pair.
    """
    if delta <= 0:
        raise ValueError(f"the effect of interest must be positive, got {delta}")
    if sd < 0:
        raise ValueError(f"SD must be non-negative, got {sd}")
    return max(1, math.ceil(round(POWER_CONSTANT * (sd / delta) ** 2, 9)))
