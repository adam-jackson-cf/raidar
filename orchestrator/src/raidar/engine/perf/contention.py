"""Trial concurrency and contention context for ``raidar perf check`` (N1, report-only).

A real experiment run's phase timings depend on how many trials shared the
host. This module reconstructs that from what runs already record: each
checked run's configured ``concurrency`` (``job.json``), every trial's
``started_at``/``finished_at`` window (Harbor ``result.json``), and the other
Raidar jobs under the same jobs root whose trials overlapped in time. Docker
work outside Raidar runs (builds, other projects) is not recorded by jobs and
so is not detectable here; the report says so rather than implying a clean host.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from raidar.engine.trial.collect import RESULT_FILE, read_result
from raidar.engine.trial.timestamps import parse_ts

JOB_MANIFEST = "job.json"
UNDETECTED_DOCKER_ACTIVITY = (
    "Docker activity outside Raidar runs (image builds, other projects) is not "
    "recorded by jobs and is not detected"
)


@dataclass(frozen=True)
class TrialWindow:
    run: str
    trial: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Contention:
    """Concurrency context over the checked runs (and overlapping sibling runs)."""

    configured_concurrency: dict[str, int | None]
    peak_concurrent_trials: int
    peak_including_other_jobs: int
    overlapping_jobs: tuple[str, ...]
    untimed_trials: int
    trial_peaks: dict[tuple[str, str], int] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "configured_concurrency": self.configured_concurrency,
            "peak_concurrent_trials": self.peak_concurrent_trials,
            "peak_including_other_jobs": self.peak_including_other_jobs,
            "overlapping_jobs": list(self.overlapping_jobs),
            "untimed_trials": self.untimed_trials,
            "other_docker_activity": UNDETECTED_DOCKER_ACTIVITY,
        }


def peak_overlap(windows: Sequence[TrialWindow]) -> int:
    """Most windows open at one instant; a window ending as another starts does not overlap."""
    events = sorted(
        [(w.start, 1) for w in windows] + [(w.end, -1) for w in windows],
        key=lambda event: (event[0], event[1]),
    )
    peak = current = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def peak_during(window: TrialWindow, windows: Sequence[TrialWindow]) -> int:
    """Most windows (this one included) open together at any instant inside ``window``."""
    instants = [window.start] + [w.start for w in windows if window.start < w.start < window.end]
    return max(sum(1 for w in windows if w.start <= instant < w.end) for instant in instants)


def read_manifest(job_dir: Path) -> dict[str, Any]:
    """The job manifest, ``{}`` when absent; malformed JSON raises ``ValueError``."""
    path = job_dir / JOB_MANIFEST
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: not a readable job manifest ({error})") from error
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: job manifest is not a JSON object")
    return loaded


def trial_window(run: str, trial: str, trial_dir: Path) -> TrialWindow | None:
    try:
        result = read_result(trial_dir) or {}
    except (OSError, ValueError) as error:
        raise ValueError(
            f"{trial_dir / RESULT_FILE}: not a readable trial result ({error})"
        ) from error
    start, end = parse_ts(result.get("started_at")), parse_ts(result.get("finished_at"))
    if start is None or end is None or end < start:
        return None
    return TrialWindow(run, trial, start, end)


def _job_windows(job_dir: Path) -> list[TrialWindow]:
    label = str(job_dir)
    trials = sorted(path for path in (job_dir / "trials").glob("*") if path.is_dir())
    return [w for path in trials if (w := trial_window(label, path.name, path)) is not None]


def _sibling_jobs(job_dirs: Sequence[Path]) -> list[Path]:
    """Other job directories under the same ``<jobs-root>/<experiment>/<job>`` roots."""
    checked = {path.resolve() for path in job_dirs}
    roots = {path.resolve().parent.parent for path in job_dirs}
    found = {
        manifest.parent.resolve() for root in roots for manifest in root.glob(f"*/*/{JOB_MANIFEST}")
    }
    return sorted(found - checked)


def other_job_windows(
    job_dirs: Sequence[Path], checked: Sequence[TrialWindow]
) -> tuple[list[TrialWindow], tuple[str, ...]]:
    """Trial windows of sibling runs that overlap the checked trials' span."""
    if not checked:
        return [], ()
    start, end = min(w.start for w in checked), max(w.end for w in checked)
    windows: list[TrialWindow] = []
    runs: list[str] = []
    for sibling in _sibling_jobs(job_dirs):
        overlapping = [w for w in _job_windows(sibling) if w.start < end and w.end > start]
        if overlapping:
            windows.extend(overlapping)
            runs.append(str(sibling))
    return windows, tuple(runs)


def measure_contention(
    job_dirs: Sequence[Path], checked: Sequence[TrialWindow], untimed: int
) -> Contention:
    """Concurrency context for the checked trials' windows."""
    configured = {
        str(job_dir): _concurrency(read_manifest(job_dir).get("concurrency"))
        for job_dir in job_dirs
    }
    others, overlapping = other_job_windows(job_dirs, checked)
    everything = [*checked, *others]
    return Contention(
        configured_concurrency=configured,
        peak_concurrent_trials=peak_overlap(checked),
        peak_including_other_jobs=peak_overlap(everything),
        overlapping_jobs=overlapping,
        untimed_trials=untimed,
        trial_peaks={(w.run, w.trial): peak_during(w, everything) for w in checked},
    )


def _concurrency(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
