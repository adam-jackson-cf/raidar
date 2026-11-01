"""Planner (D1): counterbalanced AB/BA order, seeded and reproducible."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from raidar.engine.trial.plan import (
    PlanError,
    PlanOptions,
    _check_halt_solvable,
    _check_options,
    _lock_field,
    _selection,
    arm_order,
)


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
    assert arm_order(1, 0, 0, []) == []


def _loaded(kind: str = "ab", arms: tuple[str, ...] = ("a", "b"), attempts: int = 3) -> Any:
    spec = SimpleNamespace(
        kind=kind,
        arms=[SimpleNamespace(id=arm) for arm in arms],
        analysis=SimpleNamespace(attempts=attempts),
    )
    return SimpleNamespace(spec=spec)


@pytest.mark.parametrize(
    ("kind", "options", "message"),
    [
        ("ab", PlanOptions(arms=("nope",)), "unknown arms"),
        ("ab", PlanOptions(attempts=(3,)), "attempts"),
        ("ab", PlanOptions(attempts=(-1,)), "attempts"),
        ("ab", PlanOptions(max_checkpoints=1), "smoke"),
        ("smoke", PlanOptions(max_checkpoints=0), "max_checkpoints must be >= 1"),
        ("ab", PlanOptions(concurrency=0), "concurrency must be >= 1"),
    ],
)
def test_a_selection_the_experiment_cannot_honour_is_refused(
    kind: str, options: PlanOptions, message: str
) -> None:
    with pytest.raises(PlanError, match=message):
        _check_options(_loaded(kind), options)


def test_valid_selection_options_are_accepted() -> None:
    _check_options(_loaded("smoke"), PlanOptions(arms=("a",), attempts=(0, 2), max_checkpoints=1))
    _check_options(_loaded(), PlanOptions(concurrency=1))


def _trial(arm: str, attempt: int) -> Any:
    return SimpleNamespace(trial_id=f"{arm}{attempt}", arm=arm, attempt=attempt)


def test_the_selection_narrows_what_runs_by_arm_and_attempt() -> None:
    trials = [_trial(arm, attempt) for arm in ("a", "b") for attempt in (0, 1)]
    assert _selection(trials, PlanOptions()) == {"a0", "a1", "b0", "b1"}
    assert _selection(trials, PlanOptions(arms=("a",))) == {"a0", "a1"}
    assert _selection(trials, PlanOptions(attempts=(1,))) == {"a1", "b1"}
    assert _selection(trials, PlanOptions(arms=("b",), attempts=(0,))) == {"b0"}


def test_a_halting_protocol_needs_the_problems_solvable_sets() -> None:
    with pytest.raises(PlanError, match="pins no solvable sets"):
        _check_halt_solvable("halt_on_failure", None, 0, "p@1")
    _check_halt_solvable("halt_on_failure", {"sha256": "x"}, 0, "p@1")
    _check_halt_solvable("continue", None, 0, "p@1")


def test_a_missing_lock_field_is_named() -> None:
    lock = {"execution": {"arms": {"a": {"digest": "d"}}}}
    assert _lock_field(lock, "execution", "arms", "a", "digest") == "d"
    for keys in (("execution", "problems"), ("execution", "arms", "a", "digest", "deeper")):
        with pytest.raises(PlanError, match="lock has no " + ".".join(keys)):
            _lock_field(lock, *keys)
