"""Offline grading decisions and bounded concurrency, without process or clock I/O."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from raidar.grading import offline
from raidar.grading.bundle import BundleError
from raidar.grading.coverage.offline import CoverageRun, _differences
from raidar.grading.models import ParityVerdict
from raidar.grading.per_trial import per_trial
from raidar.grading.regrade import RegradeError, RegradeOptions


@pytest.mark.parametrize("concurrency", [1, 2, 5])
async def test_bounded_concurrency_keeps_trial_order_not_completion_order(concurrency: int) -> None:
    trials = [Path(f"t{i}") for i in range(5)]
    entered: asyncio.Queue[Path] = asyncio.Queue()
    release = {trial: asyncio.Event() for trial in trials}
    finished: asyncio.Queue[Path] = asyncio.Queue()
    running: set[Path] = set()
    completed: list[Path] = []
    peak = 0

    async def work(trial: Path) -> list[str]:
        nonlocal peak
        running.add(trial)
        peak = max(peak, len(running))
        await entered.put(trial)
        await release[trial].wait()
        running.remove(trial)
        completed.append(trial)
        finished.put_nowait(trial)
        return [f"{trial.name}-a", f"{trial.name}-b"]

    task = asyncio.create_task(per_trial(trials, work, concurrency=concurrency))
    try:
        for _ in range(concurrency):
            await entered.get()
        for index in range(len(trials)):
            assert len(running) <= concurrency
            trial = max(running)
            release[trial].set()
            assert await finished.get() == trial
            if index + concurrency < len(trials):
                await entered.get()
        assert await task == [f"{trial.name}-{suffix}" for trial in trials for suffix in ("a", "b")]
        assert peak == concurrency
        if concurrency > 1:
            assert completed != trials
    finally:
        for event in release.values():
            event.set()
        await task


async def test_one_at_a_time_by_default() -> None:
    running = 0

    async def work(trial: Path) -> list[Path]:
        nonlocal running
        running += 1
        assert running == 1
        future = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(future.set_result, None)
        await future
        running -= 1
        return [trial]

    assert await per_trial([Path("a"), Path("b")], work) == [Path("a"), Path("b")]


@pytest.mark.parametrize("concurrency", [-1, 0])
async def test_concurrency_below_one_is_refused(concurrency: int) -> None:
    async def work(trial: Path) -> list[Path]:
        return [trial]

    with pytest.raises(ValueError, match="at least 1"):
        await per_trial([Path("a")], work, concurrency=concurrency)


@pytest.mark.parametrize(
    ("eligible", "differences", "fork", "changed"),
    [
        (True, (), {"baseline": {"lines": [1]}}, ()),
        (False, (), {"baseline": {"lines": [1]}}, ("parity",)),
        (True, ("case:a",), {"baseline": {"lines": [1]}}, ("parity differences",)),
        (True, (), None, ("g-kernel-input.json",)),
        (True, (), {"baseline": {"lines": [2]}}, ("g-kernel-input.json",)),
        (
            False,
            ("case:b",),
            {"produced": {}},
            ("parity", "parity differences", "g-kernel-input.json"),
        ),
    ],
)
def test_coverage_replay_reports_every_changed_evidence_dimension(
    eligible: bool,
    differences: tuple[str, ...],
    fork: dict[str, object] | None,
    changed: tuple[str, ...],
) -> None:
    expected = (ParityVerdict(eligible=True), (), {"baseline": {"lines": [1]}})
    run = CoverageRun(ParityVerdict(eligible=eligible), differences, fork, "grader-id", 0.0, None)
    found = _differences(expected, run)
    assert tuple(change.split(":", 1)[0] for change in found) == changed
    assert not _differences((run.parity, run.differences, run.fork), run)


@pytest.mark.parametrize("error_type", [RegradeError, BundleError])
@pytest.mark.parametrize(
    "operation", ["regrade", "grade_pre_fix", "produce_coverage", "check_coverage"]
)
def test_offline_infrastructure_errors_are_refused_not_scored(
    monkeypatch: pytest.MonkeyPatch, operation: str, error_type: type[Exception]
) -> None:
    failure = error_type("retained grading evidence unavailable")

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise failure

    seam = {
        "regrade": "regrade_path",
        "grade_pre_fix": "grade_pre_fix_path",
        "produce_coverage": "produce_coverage_path",
        "check_coverage": "check_coverage_path",
    }[operation]
    monkeypatch.setattr(offline, seam, unavailable)
    target, store, options = Path("job"), Path("store"), RegradeOptions(docker_context="ctx")
    actions = {
        "regrade": lambda: offline.regrade(target, options, store),
        "grade_pre_fix": lambda: offline.grade_pre_fix(
            target, store, ["verification-effort@1"], options, replay=False
        ),
        "produce_coverage": lambda: offline.produce_coverage(
            target, store, ["code-quality@9"], options, replay=False
        ),
        "check_coverage": lambda: offline.check_coverage(target, options, store),
    }
    with pytest.raises(offline.OfflineGradingError) as refused:
        actions[operation]()
    assert refused.value.__cause__ is failure
    assert str(failure) in str(refused.value)
