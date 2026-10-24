"""Job-level target guard: reconnect before each trial, reap after a lost one.

A remote Docker daemon can vanish mid-job (laptop sleep, tunnel drop). The
guard keeps a job honest about that:

- ``admit`` runs before each trial starts: it ``ensure``s the target, retrying
  with exponential backoff for up to ``RAIDAR_TARGET_RECONNECT_S`` (default
  300). If the target stays down it raises ``TargetLostError`` and the job
  stops scheduling; running trials finish or fail on their own.
- ``review`` runs on each finished trial. An *invalid* trial whose target is
  unreachable now, or was at any point since the trial was admitted, gets a
  failing ``target_reachable`` validity check (an infra-excluded trial, never a
  silent result). The guard then reconnects and removes that trial's Compose
  project, recording what it did in ``raidar-target-reap.json`` beside the trial.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raidar.contract.trial import ExecutionValidity, TrialRecord, ValidityCheck
from raidar.engine.target.config import Target, TargetError
from raidar.engine.target.health import TargetUnavailableError, ensure_target
from raidar.engine.target.transport import docker_healthy

ENV_RECONNECT_S = "RAIDAR_TARGET_RECONNECT_S"
DEFAULT_RECONNECT_S = 300.0
REAP_FILE = "raidar-target-reap.json"
TARGET_REACHABLE_CHECK = "target_reachable"
_FIRST_DELAY_S = 1.0
_MAX_DELAY_S = 30.0


class TargetLostError(TargetError):
    """The target stayed unreachable past the reconnect budget; the job stops."""


def reconnect_budget_s() -> float:
    raw = os.environ.get(ENV_RECONNECT_S)
    if raw is None:
        return DEFAULT_RECONNECT_S
    try:
        return max(0.0, float(raw))
    except ValueError as error:
        raise TargetError(f"{ENV_RECONNECT_S} must be a number of seconds, not {raw!r}") from error


@dataclass
class TargetGuard:
    target: Target
    healthy: Callable[[], bool]
    ensure: Callable[[], None]
    reap: Callable[[str, Path], dict[str, Any]]
    reconnect_s: float = DEFAULT_RECONNECT_S
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    outages: int = 0
    _started: dict[str, int] = field(default_factory=dict, repr=False)
    _lost: TargetLostError | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def for_target(
        cls,
        target: Target,
        reap: Callable[[str, Path], dict[str, Any]],
        reconnect_s: float | None = None,
    ) -> TargetGuard:
        return cls(
            target=target,
            healthy=lambda: docker_healthy(target.docker_context),
            ensure=lambda: ensure_target(target),
            reap=reap,
            reconnect_s=reconnect_budget_s() if reconnect_s is None else reconnect_s,
        )

    async def _reconnect(self) -> None:
        async with self._lock:
            if self._lost is not None:
                raise self._lost
            deadline = self.clock() + self.reconnect_s
            delay = _FIRST_DELAY_S
            failed = False
            while True:
                try:
                    await asyncio.to_thread(self.ensure)
                    return
                except TargetUnavailableError as error:
                    if not failed:
                        self.outages += 1
                        failed = True
                    if self.clock() + delay > deadline:
                        self._lost = TargetLostError(
                            f"target {self.target.name!r} stayed down for "
                            f"{self.reconnect_s:.0f}s: {error}"
                        )
                        raise self._lost from error
                await self.sleep(delay)
                delay = min(delay * 2, _MAX_DELAY_S)

    async def admit(self, trial_id: str) -> None:
        """Before a trial starts: the target is ensured, or the job stops (``TargetLostError``)."""
        await self._reconnect()
        self._started[trial_id] = self.outages

    async def review(
        self, record: TrialRecord, *, trial_dir: Path, trials_dir: Path
    ) -> TrialRecord:
        """After a trial: mark and reap an invalid trial that lost its target."""
        started = self._started.pop(record.trial.trial_id, self.outages)
        if record.valid:
            return record
        reachable = await asyncio.to_thread(self.healthy)
        if reachable and self.outages == started:
            return record
        if not reachable:
            self.outages += 1
        detail = (
            f"target {self.target.name!r} was unreachable "
            f"{'when the trial ended' if not reachable else 'during the trial'}"
        )
        marked = record.model_copy(
            update={
                "validity": ExecutionValidity(
                    checks=(
                        *record.validity.checks,
                        ValidityCheck(name=TARGET_REACHABLE_CHECK, passed=False, detail=detail),
                    )
                )
            }
        )
        trial_id = record.trial.trial_id
        reaped: dict[str, Any] = {"trial": trial_id, "docker_context": self.target.docker_context}
        try:
            await self._reconnect()
            reaped |= {
                "reconnected": True,
                "result": await asyncio.to_thread(self.reap, trial_id, trials_dir),
            }
        except TargetLostError as error:
            reaped |= {"reconnected": False, "error": str(error)}
        trial_dir.mkdir(parents=True, exist_ok=True)
        (trial_dir / REAP_FILE).write_text(json.dumps(reaped, indent=2, sort_keys=True) + "\n")
        return marked
