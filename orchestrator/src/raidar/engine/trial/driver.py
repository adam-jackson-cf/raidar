"""Trial scheduling and the Harbor trial runner (plan D1 concurrency, D2).

``run_in_order`` starts jobs strictly in plan order and never has more than
``concurrency`` running at once. ``run_harbor_trial`` is the one place a
Harbor trial starts: the Raidar verifier's pre-agent leak guard is installed
before ``trial.run()``, as ``RaidarVerifier`` requires (it fails closed
without it), together with the agent-end reaper (``raidar.engine.health.reaper``) that
kills agent processes before artifacts are collected and graded, and the
provisioning hooks of any component that installs a tool in the trial
container (``raidar.subject.treatment.runtime.provision``; trial provisions install at
environment start), then the T1 tool-tree hash (``raidar.engine.health.tool_tree``) and the
host-side liveness monitor (``raidar.engine.health.liveness``, enforce by default).

Decision (deviates from plan section 1 "Harbor JobConfigs", N4 "Harbor owns
concurrency"): Raidar builds one Harbor ``TrialConfig`` per planned trial and
schedules them itself instead of submitting a ``JobConfig``. Reasons:

- the leak guard must be installed on the ``Trial`` object between
  ``Trial.create`` and ``Trial.run``; ``Job`` creates and runs trials
  internally and its ``on_agent_started`` hook fires after the agent's first
  step has already begun, which is too late for a pre-agent scan;
- each trial runs its own materialised task (per-arm ``setup.sh``/overlay,
  per-trial ``min_reward``), which a single ``JobConfig`` task list cannot
  express without one job per trial;
- plan order (the seeded, counterbalanced arm order) must be the start order.

Harbor still owns everything inside a trial; Raidar owns only the start
order and the concurrency bound (``run_in_order``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from harbor.models.trial.config import TrialConfig
from harbor.trial.trial import Trial

from raidar.engine.health.liveness import install_liveness_monitor
from raidar.engine.health.reaper import install_agent_reaper
from raidar.engine.health.tool_tree import install_tool_tree_watch
from raidar.grading.guard import install_leak_guard
from raidar.subject.treatment.runtime.provision import AnyProvision, install_trial_provisioning

JobT = TypeVar("JobT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class TrialRun:
    trial_dir: Path
    exception_type: str | None


class TrialRunner(Protocol):
    async def __call__(
        self, config: TrialConfig, bundle_root: Path, provisions: Sequence[AnyProvision] = ()
    ) -> TrialRun: ...


async def run_harbor_trial(
    config: TrialConfig, bundle_root: Path, provisions: Sequence[AnyProvision] = ()
) -> TrialRun:
    trial = await Trial.create(config)
    install_leak_guard(trial, bundle_root)
    install_agent_reaper(trial, bundle_root)
    install_trial_provisioning(trial, provisions)
    install_tool_tree_watch(trial, bundle_root)
    install_liveness_monitor(trial)
    result = await trial.run()
    error = result.exception_info.exception_type if result.exception_info else None
    return TrialRun(trial.paths.trial_dir, error)


async def run_in_order(
    jobs: Sequence[JobT],
    worker: Callable[[JobT], Awaitable[ResultT]],
    *,
    concurrency: int,
    admit: Callable[[JobT], Awaitable[None]] | None = None,
) -> list[ResultT]:
    """Run ``worker`` over ``jobs``: started in order, at most ``concurrency`` at a time.

    Workers are expected to contain their own failures (``run.run_trial``
    records one as an infra error). If one raises anyway, no further job is
    started, every job still running is cancelled and awaited, and the first
    exception propagates: nothing is left running behind the caller.

    ``admit`` runs before each job starts. If it raises, no further job is
    started, but jobs already running are left to finish on their own before
    the exception propagates (a lost Docker target: running trials fail or
    complete by themselves).
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    slots = asyncio.Semaphore(concurrency)

    async def guarded(job: JobT) -> ResultT:
        try:
            return await worker(job)
        finally:
            slots.release()

    tasks: list[asyncio.Task[ResultT]] = []
    try:
        for job in jobs:
            await slots.acquire()
            _raise_first_failure(tasks)
            if admit is not None:
                try:
                    await admit(job)
                except Exception:
                    slots.release()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
            tasks.append(asyncio.create_task(guarded(job)))
        return list(await asyncio.gather(*tasks))
    finally:
        running = [task for task in tasks if not task.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


def _raise_first_failure[T](tasks: Sequence[asyncio.Task[T]]) -> None:
    for task in tasks:
        if task.done() and not task.cancelled():
            error = task.exception()
            if error is not None:
                raise error
