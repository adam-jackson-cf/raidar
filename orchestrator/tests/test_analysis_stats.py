"""Paired-statistics primitives: t quantiles, intervals, exact McNemar, power (WS-J)."""

from __future__ import annotations

import pytest

from raidar.evaluation.analysis.stats import (
    BOOTSTRAP_MIN_PAIRS,
    IntervalMethod,
    mcnemar_exact,
    pairs_for_power,
    summarise,
    t_cdf,
    t_quantile,
)


@pytest.mark.parametrize(
    ("df", "expected"),
    [(1, 12.706205), (2, 4.302653), (4, 2.776445), (9, 2.262157), (30, 2.042272)],
)
def test_t_quantile_matches_published_two_sided_95_values(df: int, expected: float) -> None:
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=1e-5)
    assert t_cdf(t_quantile(0.975, df), df) == pytest.approx(0.975, abs=1e-12)


def test_interval_method_switches_to_bootstrap_at_ten_pairs() -> None:
    below = summarise(
        [0.1 * i for i in range(BOOTSTRAP_MIN_PAIRS - 1)], seed=1, bootstrap_samples=500
    )
    at = summarise([0.1 * i for i in range(BOOTSTRAP_MIN_PAIRS)], seed=1, bootstrap_samples=500)
    assert below.interval is not None
    assert below.interval.method is IntervalMethod.PAIRED_T
    assert at.interval is not None
    assert at.interval.method is IntervalMethod.BOOTSTRAP
    assert at.interval.low <= at.mean <= at.interval.high  # type: ignore[operator]


def test_paired_t_interval_is_mean_plus_minus_t_times_se() -> None:
    summary = summarise([1.0, 2.0, 3.0, 4.0, 5.0], seed=0, bootstrap_samples=500)
    assert (summary.mean, summary.sd) == (3.0, pytest.approx(1.5811388))
    half = 2.776445 * 1.5811388 / 5**0.5
    assert summary.interval is not None
    assert (summary.interval.low, summary.interval.high) == (
        pytest.approx(3.0 - half, abs=1e-5),
        pytest.approx(3.0 + half, abs=1e-5),
    )


def test_fewer_than_two_values_have_no_spread_or_interval() -> None:
    assert summarise([], seed=0, bootstrap_samples=500).mean is None
    single = summarise([0.4], seed=0, bootstrap_samples=500)
    assert (single.mean, single.sd, single.interval) == (0.4, None, None)


def test_exact_mcnemar_counts_only_discordant_pairs() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(1, 9) == mcnemar_exact(9, 1) == pytest.approx(0.021484375)
    assert mcnemar_exact(3, 3) == 1.0


@pytest.mark.parametrize(
    ("sd", "delta", "pairs"),
    [(1.0, 1.0, 8), (1.0, 0.5, 32), (1.0, 0.25, 126), (2.5, 1.0, 49), (0.1, 0.125, 6)],
)
def test_pairs_for_power_is_the_ceiling_of_7_84_sd_over_delta_squared(
    sd: float, delta: float, pairs: int
) -> None:
    assert pairs_for_power(sd, delta) == pairs


def test_pairs_for_power_rejects_a_non_positive_effect() -> None:
    with pytest.raises(ValueError, match="positive"):
        pairs_for_power(1.0, 0.0)
