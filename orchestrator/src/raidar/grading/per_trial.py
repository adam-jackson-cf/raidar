"""Run one trial's offline grading work beside other trials' (``raidar assess --concurrency``).

The offline coverage runs (``raidar.grading.coverage.offline``) and the pre-fix
grades (``raidar.grading.prefix.grade``) of a job used to be made one trial
after another: a 2-trial explore smoke of the guardrails experiment spent about
44 minutes there before any scorer ran. Each trial's work stays sequential (its
steps in order, as before); up to ``concurrency`` trials run at once, each step
in its own grader container. The entries come back in trial order, so a
report does not depend on which trial finished first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path


async def per_trial[T](
    trials: Sequence[Path], work: Callable[[Path], Awaitable[list[T]]], concurrency: int = 1
) -> list[T]:
    """``work`` for every trial, at most ``concurrency`` at a time; results in trial order."""
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1, got {concurrency}")
    gate = asyncio.Semaphore(concurrency)

    async def one(trial: Path) -> list[T]:
        async with gate:
            return await work(trial)

    chunks = await asyncio.gather(*(one(trial) for trial in trials))
    return [item for chunk in chunks for item in chunk]


__all__ = ["per_trial"]
