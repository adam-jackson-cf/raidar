"""The ``raidar perf check`` report over real runs: budgets, warnings and contention.

On real experiment runs the check is REPORT-ONLY (user decision 2026-10-01):
every phase is reported against its accepted budget, breaches become
warnings, and the run's trial concurrency and contention context is recorded
beside them. ``--strict`` turns breaches back into a failure. The enforced
overhead gate is the controlled no-LLM fixture (``make perf-gate``), which
runs this check strictly.

For a breached p95 budget the report also splits the samples by how many
trials were open while each trial ran (at most ``CONCURRENCY_SPLIT`` vs more),
so a miss under heavy contention is told apart from one at pilot concurrency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.execution.budgets import (
    PASSING,
    PerfInputError,
    PhaseCheck,
    RunSamples,
    Verdict,
    check_budgets,
)
from raidar.execution.contention import (
    Contention,
    TrialWindow,
    measure_contention,
    trial_window,
)

# Pilot concurrency is 3: a breach is split at <= 3 vs >= 4 concurrent trials.
CONCURRENCY_SPLIT = 3


@dataclass(frozen=True)
class SplitCheck:
    """A breached budget re-judged over the trials at one contention level."""

    label: str
    check: PhaseCheck

    def to_json(self) -> dict[str, object]:
        return {"concurrent_trials": self.label, **self.check.to_json()}


@dataclass(frozen=True)
class PerfReport:
    checks: list[PhaseCheck]
    contention: Contention
    splits: dict[int, list[SplitCheck]]  # index into ``checks``

    @property
    def breaches(self) -> list[PhaseCheck]:
        return [check for check in self.checks if check.verdict not in PASSING]

    @property
    def passed(self) -> bool:
        return not self.breaches


def _windows(loaded: RunSamples) -> tuple[list[TrialWindow], int]:
    windows = [trial_window(t.run, t.trial, t.trial_dir) for t in loaded.trials]
    timed = [window for window in windows if window is not None]
    return timed, len(windows) - len(timed)


def _split(
    loaded: RunSamples, check: PhaseCheck, peaks: dict[tuple[str, str], int], min_samples: int
) -> list[SplitCheck]:
    buckets = {
        f"<={CONCURRENCY_SPLIT}": lambda peak: peak <= CONCURRENCY_SPLIT,
        f">={CONCURRENCY_SPLIT + 1}": lambda peak: peak > CONCURRENCY_SPLIT,
    }
    splits = []
    for label, keep in buckets.items():
        samples = [
            sample
            for trial in loaded.trials
            if (peak := peaks.get((trial.run, trial.trial))) is not None and keep(peak)
            for sample in trial.samples
        ]
        judged = check_budgets(
            samples, budgets=(check.budget,), min_samples=min_samples, multi_step=True
        )
        if judged and judged[0].samples:
            splits.append(SplitCheck(label, judged[0]))
    return splits


def build_report(run_dirs: Sequence[Path], loaded: RunSamples, *, min_samples: int) -> PerfReport:
    """Checks, contention and per-contention splits of each breached p95 budget."""
    checks = check_budgets(loaded.samples, min_samples=min_samples, multi_step=loaded.multi_step)
    try:
        windows, untimed = _windows(loaded)
        contention = measure_contention(run_dirs, windows, untimed)
    except ValueError as error:  # malformed run.json / result.json, not a budget breach
        raise PerfInputError(str(error)) from error
    splits = {
        index: _split(loaded, check, contention.trial_peaks, min_samples)
        for index, check in enumerate(checks)
        if check.verdict is Verdict.FAIL and not check.budget.tripwire
    }
    return PerfReport(checks, contention, splits)
