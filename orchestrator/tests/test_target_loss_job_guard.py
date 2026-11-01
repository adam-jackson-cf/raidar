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
from raidar.engine.target import guard as guard_module
from raidar.engine.target.config import Selection
from raidar.engine.target.guard import (
    ENV_RECONNECT_S,
    REAP_FILE,
    TargetGuard,
    TargetLostError,
    reconnect_budget_s,
)
from raidar.engine.target.health import DockerFacts, TargetUnavailableError
from raidar.engine.target.job import MECHANICS_EPOCH, JobTarget
from raidar.engine.trial.driver import run_in_order
from tests.execution_support import fake_engine_target

HASHES = TrialHashes(
    lock="l",
    problem="p",
    arm="a",
    workflow="w",
    packages="s",
    primitive_code="s",
    agent="g",
    protocol="r",
    trial="t",
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

    def ensure(self, timeout_s: float) -> None:
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


@pytest.mark.parametrize("probe_seconds", [0.2, 2.0, 30.0])
async def test_reconnect_shares_its_remaining_budget_with_slow_ensure_calls(
    probe_seconds: float,
) -> None:
    target = FakeTarget()
    guard = target.guard(reconnect_s=5.0)
    budgets: list[float] = []

    def slow_ensure(timeout_s: float) -> None:
        budgets.append(timeout_s)
        target.now += min(probe_seconds, timeout_s)
        raise TargetUnavailableError("probe timed out")

    guard.ensure = slow_ensure
    with pytest.raises(TargetLostError):
        await guard.admit("t0")

    assert budgets[0] == 5.0
    assert all(0 <= seconds <= 5.0 for seconds in budgets)
    assert target.now <= 5.0


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
    release_second = asyncio.Event()
    started: list[int] = []
    finished: list[int] = []

    async def worker(job: int) -> int:
        started.append(job)
        if job == 1:
            await release_second.wait()
        finished.append(job)
        return job

    async def admit(job: int) -> None:
        if job == 2:
            target.up = False
        try:
            await guard.admit(str(job))
        except TargetLostError:
            assert finished == [0], "trial 1 is still in flight when the target is lost"
            release_second.set()
            raise

    with pytest.raises(TargetLostError):
        await run_in_order(list(range(5)), worker, concurrency=2, admit=admit)

    assert started == [0, 1]
    assert sorted(finished) == [0, 1]  # the in-flight trial was not cancelled


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 300.0), ("45", 45.0), ("-5", 0.0), ("0.5", 0.5)],
)
def test_the_reconnect_budget_comes_from_the_environment_never_negative(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float
) -> None:
    monkeypatch.delenv(ENV_RECONNECT_S, raising=False)
    if raw is not None:
        monkeypatch.setenv(ENV_RECONNECT_S, raw)

    assert reconnect_budget_s() == expected


def test_a_reconnect_budget_that_is_not_a_number_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_RECONNECT_S, "soon")

    with pytest.raises(guard_module.TargetError, match="number of seconds"):
        reconnect_budget_s()


def test_a_guard_for_a_target_probes_and_ensures_that_targets_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, object]] = []
    target = fake_engine_target("ctx")
    monkeypatch.setattr(
        guard_module, "docker_healthy", lambda context: seen.append(("health", context)) or True
    )
    monkeypatch.setattr(
        guard_module,
        "ensure_target",
        lambda chosen, timeout_s: seen.append(("ensure", (chosen, timeout_s))),
    )
    monkeypatch.setenv(ENV_RECONNECT_S, "12")

    guard = TargetGuard.for_target(target, reap=lambda _t, _d: {})
    explicit = TargetGuard.for_target(target, reap=lambda _t, _d: {}, reconnect_s=3.0)

    assert guard.healthy() is True
    guard.ensure(7.0)
    assert seen == [("health", target.docker_context), ("ensure", (target, 7.0))]
    assert (guard.reconnect_s, explicit.reconnect_s) == (12.0, 3.0)


@pytest.mark.parametrize("facts", [None, DockerFacts("29.0", "OrbStack", "aarch64", 8, 1024)])
def test_the_job_target_records_where_the_job_ran_and_under_which_mechanics(
    facts: DockerFacts | None,
) -> None:
    trial = fake_engine_target("trial-ctx")

    fields = JobTarget(Selection.of(trial), facts).manifest_fields(requested_concurrency=64)

    recorded = fields["execution_target"]
    assert fields["mechanics_epoch"] == MECHANICS_EPOCH
    assert recorded["docker_context"] == "trial-ctx"
    assert recorded["transport"] is None
    assert recorded["effective_concurrency"] == trial.effective_concurrency(64)
    assert recorded["docker"] == (facts.as_record() if facts else None)
    assert recorded["grader"] == {"name": trial.name, "docker_context": "trial-ctx"}
