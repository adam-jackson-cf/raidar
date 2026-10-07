"""``raidar.verifier.per_trial``: up to N trials' offline grading work at once, results in order."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from raidar.verifier.per_trial import per_trial


async def test_at_most_n_trials_run_at_once_and_results_keep_trial_order() -> None:
    trials = [Path(f"t{i}") for i in range(5)]
    running: list[int] = []
    peak = 0

    async def work(trial: Path) -> list[str]:
        nonlocal peak
        running.append(1)
        peak = max(peak, len(running))
        # Later trials finish first: the result order must not follow completion order.
        await asyncio.sleep(0.01 * (5 - int(trial.name[1:])))
        running.pop()
        return [f"{trial.name}-a", f"{trial.name}-b"]

    result = await per_trial(trials, work, concurrency=2)

    assert result == [f"t{i}-{s}" for i in range(5) for s in ("a", "b")]
    assert peak == 2


async def test_one_at_a_time_by_default() -> None:
    peak, running = 0, 0

    async def work(trial: Path) -> list[Path]:
        nonlocal peak, running
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return [trial]

    assert await per_trial([Path("a"), Path("b")], work) == [Path("a"), Path("b")]
    assert peak == 1


async def test_concurrency_below_one_is_refused() -> None:
    async def work(trial: Path) -> list[Path]:
        return [trial]

    with pytest.raises(ValueError, match="at least 1"):
        await per_trial([Path("a")], work, concurrency=0)
