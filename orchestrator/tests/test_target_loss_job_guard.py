"""The job guard: reconnect before each trial, mark and reap a trial that lost its target."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from raidar.contract.trial import (
    ExecutionValidity,
    PlannedTrial,
    ProgressionRule,
    TrialHashes,
    TrialRecord,
    ValidityCheck,
)
from raidar.engine.target.guard import REAP_FILE, TargetGuard, TargetLostError
from raidar.engine.target.health import TargetUnavailableError
from raidar.engine.trial.driver import run_in_order
from tests.execution_support import fake_engine_target

HASHES = TrialHashes(
    lock="l", problem="p", arm="a", condition="c", agent="g", protocol="r", trial="t"
)


def record(trial_id: str, *, valid: bool = True) -> TrialRecord:
    return TrialRecord(
        experiment="e",
        job_id="j",
        trial=PlannedTrial(
            trial_id=trial_id,
            attempt=0,
            order=0,
            sequence=0,
            arm="a",
            problem="p",
            problem_index=0,
            steps=("checkpoint_1",),
            hashes=HASHES,
        ),
        trial_dir=f"trials/{trial_id}",
        progression=ProgressionRule(mode="continue", reward_key="strict_pass_rate"),
        step_results=(),
        reached=(),
        reached_source="derived",
        validity=ExecutionValidity(checks=(ValidityCheck(name="trial_completed", passed=valid),)),
    )


class FakeTarget:
    """A daemon that is up or down; ``ensure`` fails while down."""

    def __init__(self) -> None:
        self.up = True
        self.ensures = 0
        self.reaped: list[str] = []
        self.slept: list[float] = []
        self.now = 0.0
        self.recover_after: int | None = None  # ensure calls that fail before it recovers

    def healthy(self) -> bool:
        return self.up

    def ensure(self) -> None:
        self.ensures += 1
        if self.recover_after is not None and not self.up:
            self.recover_after -= 1
            if self.recover_after <= 0:
                self.up = True
        if not self.up:
            raise TargetUnavailableError("daemon down")

    def reap(self, trial_id: str, trials_dir: Path) -> dict[str, Any]:
        project = f"{trial_id}__env-{trials_dir.parent.name}"
        self.reaped.append(project)
        return {"project": project, "returncode": 0}

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def guard(self, reconnect_s: float = 300.0) -> TargetGuard:
        return TargetGuard(
            target=fake_engine_target("ctx"),
            healthy=self.healthy,
            ensure=self.ensure,
            reap=self.reap,
            reconnect_s=reconnect_s,
            sleep=self.sleep,
            clock=lambda: self.now,
        )


async def test_a_trial_that_ends_valid_is_left_alone(tmp_path: Path) -> None:
    target = FakeTarget()
    guard = target.guard()
    await guard.admit("t0")

    reviewed = await guard.review(
        record("t0"), trial_dir=tmp_path / "t0", trials_dir=tmp_path / "x" / "trials"
    )

    assert reviewed.valid
    assert target.reaped == []
    assert not (tmp_path / "t0" / REAP_FILE).exists()


async def test_an_invalid_trial_on_a_healthy_target_is_not_blamed_on_it(tmp_path: Path) -> None:
    target = FakeTarget()
    guard = target.guard()
    await guard.admit("t0")

    reviewed = await guard.review(
        record("t0", valid=False), trial_dir=tmp_path / "t0", trials_dir=tmp_path / "x" / "trials"
    )

    assert [c.name for c in reviewed.validity.checks] == ["trial_completed"]
    assert target.reaped == []


async def test_a_trial_that_loses_its_target_is_marked_reconnected_and_reaped(
    tmp_path: Path,
) -> None:
    target = FakeTarget()
    guard = target.guard()
    await guard.admit("t0")
    target.up = False
    target.recover_after = 2  # two failed reconnect attempts, then the daemon is back

    reviewed = await guard.review(
        record("t0", valid=False), trial_dir=tmp_path / "t0", trials_dir=tmp_path / "abc" / "trials"
    )

    check = reviewed.validity.checks[-1]
    assert (check.name, check.passed) == ("target_reachable", False)
    assert "unreachable" in check.detail
    assert not reviewed.valid
    assert target.reaped == ["t0__env-abc"]
    assert target.slept == [1.0]  # exponential backoff between failed reconnects
    reaped = json.loads((tmp_path / "t0" / REAP_FILE).read_text())
    assert reaped["result"]["project"] == "t0__env-abc"
    assert reaped["reconnected"] is True
    assert reaped["result"]["returncode"] == 0


async def test_a_trial_admitted_before_a_healed_outage_is_marked_on_review(
    tmp_path: Path,
) -> None:
    target = FakeTarget()
    guard = target.guard()
    await guard.admit("slow")
    target.up = False
    target.recover_after = 2
    await guard.admit("next")  # ensure fails once (an outage is counted), then succeeds

    reviewed = await guard.review(
        record("slow", valid=False),
        trial_dir=tmp_path / "slow",
        trials_dir=tmp_path / "abc" / "trials",
    )

    assert reviewed.validity.checks[-1].name == "target_reachable"
    assert "during the trial" in reviewed.validity.checks[-1].detail
    assert target.reaped == ["slow__env-abc"]


async def test_a_persistent_loss_raises_target_lost_and_stays_lost(tmp_path: Path) -> None:
    target = FakeTarget()
    guard = target.guard(reconnect_s=10.0)
    target.up = False

    with pytest.raises(TargetLostError, match="stayed down"):
        await guard.admit("t0")
    ensures = target.ensures
    with pytest.raises(TargetLostError):
        await guard.admit("t1")

    assert target.ensures == ensures  # a lost target is not retried per trial
    assert sum(target.slept) <= 10.0


async def test_review_records_the_loss_when_the_target_never_returns(tmp_path: Path) -> None:
    target = FakeTarget()
    guard = target.guard(reconnect_s=5.0)
    await guard.admit("t0")
    target.up = False

    reviewed = await guard.review(
        record("t0", valid=False), trial_dir=tmp_path / "t0", trials_dir=tmp_path / "abc" / "trials"
    )

    assert reviewed.validity.checks[-1].name == "target_reachable"
    assert target.reaped == []
    reaped = json.loads((tmp_path / "t0" / REAP_FILE).read_text())
    assert reaped["reconnected"] is False


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
