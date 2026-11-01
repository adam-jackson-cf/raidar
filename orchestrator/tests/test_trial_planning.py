"""Planner (D1): counterbalanced AB/BA order, seeded and reproducible."""

from __future__ import annotations

import pytest

from raidar.engine.trial.plan import arm_order


@pytest.mark.parametrize("arms", [["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]])
def test_arm_order_is_counterbalanced_seeded_and_reproducible(arms: list[str]) -> None:
    n = len(arms)
    attempts = 6 * n
    orders = [tuple(arm_order(20260929, attempt, 0, arms)) for attempt in range(attempts)]
    assert all(sorted(order) == arms for order in orders)
    # Every complete cycle of len(arms) attempts puts each arm in each position once
    # (AB/BA for two arms), so start-order effects cancel within the cycle.
    for start in range(0, attempts, n):
        cycle = orders[start : start + n]
        for position in range(n):
            assert sorted(order[position] for order in cycle) == arms
    assert orders == [tuple(arm_order(20260929, attempt, 0, arms)) for attempt in range(attempts)]
    # The lead arm of each cycle is drawn from the seed, not fixed.
    assert len({orders[start] for start in range(0, attempts, n)}) > 1
    other_seed = [tuple(arm_order(7, attempt, 0, arms)) for attempt in range(attempts)]
    assert other_seed != orders
