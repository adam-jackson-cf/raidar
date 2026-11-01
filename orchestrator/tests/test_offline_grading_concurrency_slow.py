"""Real-clock offline grading concurrency seam."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from raidar.grading.per_trial import per_trial

pytestmark = pytest.mark.slow


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
