"""In-flight scheduling across target loss with real-clock workers."""

from __future__ import annotations

import asyncio

import pytest

from raidar.engine.target.guard import TargetLostError
from raidar.engine.trial.driver import run_in_order
from tests.test_target_loss_job_guard import FakeTarget

pytestmark = pytest.mark.slow


async def test_a_lost_target_stops_scheduling_but_running_trials_finish() -> None:
    target = FakeTarget()
    guard = target.guard(reconnect_s=0.0)
    started: list[int] = []
    finished: list[int] = []

    async def worker(job: int) -> int:
        started.append(job)
        await asyncio.sleep(0.05 if job == 0 else 0.2)
        finished.append(job)
        return job

    async def admit(job: int) -> None:
        if job == 2:
            target.up = False
        await guard.admit(str(job))

    with pytest.raises(TargetLostError):
        await run_in_order(list(range(5)), worker, concurrency=2, admit=admit)

    assert started == [0, 1]
    assert sorted(finished) == [0, 1]  # in-flight trials were not cancelled
