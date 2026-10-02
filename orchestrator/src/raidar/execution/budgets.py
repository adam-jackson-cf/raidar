"""N1 overhead budgets and ``raidar perf check`` (plan D4; requirements N1).

Budgets are the ACCEPTED table in docs/reboot/overhead-baseline.md §5
(user, 2026-09-29), keyed by the Phase 0 phase names and, where the table
splits a budget, by condition. The rule: a budgeted phase fails when the p95
over at least ``min_samples`` (10) samples exceeds its budget. Cold
environment starts and cold grader-image builds are record-only with a
tripwire: any single cold sample above it fails. Percentiles interpolate
linearly between order statistics, as in the baseline.

A budget over a gap *between* steps (``step_archive_gap``) is undefined when
every checked trial runs a single checkpoint (a one-shot K=1 problem,
F1, or a run truncated to one checkpoint): it is then ``not_applicable``,
which passes, rather than ``missing``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from raidar.execution.models import PhaseSample, TrialRecord
from raidar.job_layout import TRIAL_RECORD_GLOB, is_legacy_job, legacy_job_message

MIN_SAMPLES = 10


@dataclass(frozen=True)
class Budget:
    phase: str
    seconds: float
    condition: str | None = None
    tripwire: bool = False
    note: str = ""
    multi_step_only: bool = False


# docs/reboot/overhead-baseline.md §5 (accepted). ``condition`` None matches any.
ACCEPTED_BUDGETS: tuple[Budget, ...] = (
    Budget("environment_start", 20.0, "warm"),
    Budget("environment_start", 420.0, "cold", tripwire=True, note="record-only; tripwire"),
    Budget("grader_image", 1.0, "warm", note="presence check"),
    Budget("grader_image", 180.0, "cold", tripwire=True, note="cold build; tripwire"),
    Budget(
        "component_apply",
        2.5,
        note="provisional; host-side compile+write only (in-container share is per_step_reset)",
    ),
    Budget("per_step_reset", 2.0),
    Budget("artifact_collection", 3.5, note="small /app; size term pending"),
    Budget("snapshot_transfer", 0.5, "artifact"),
    Budget("snapshot_transfer", 2.0, "download"),
    Budget("verifier_plumbing", 1.5),
    Budget("step_archive_gap", 2.5, multi_step_only=True, note="between steps; K>=2 only"),
    Budget("teardown_result_write", 4.0, "init"),
    Budget("teardown_result_write", 15.0, "no-init"),
    Budget("raidar_result_write", 0.5),
    Budget("per_step_overhead", 10.0),
    Budget("per_trial_fixed_overhead", 27.0, "init"),
    Budget("per_trial_fixed_overhead", 38.0, "no-init"),
)
# Budgeted in N1 but not produced by an execution run.
NOT_RECORDED_BY_RUNS = {
    "scorer_orchestration": "scorers run in `raidar assess`, not in `raidar run` (6 s/scorer)",
}


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT = "insufficient"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


PASSING = frozenset({Verdict.PASS, Verdict.NOT_APPLICABLE})


@dataclass(frozen=True)
class PhaseCheck:
    budget: Budget
    samples: int
    p50: float | None
    p95: float | None
    maximum: float | None
    verdict: Verdict

    def to_json(self) -> dict[str, object]:
        return {
            "phase": self.budget.phase,
            "condition": self.budget.condition,
            "budget_s": self.budget.seconds,
            "tripwire": self.budget.tripwire,
            "samples": self.samples,
            "p50_s": self.p50,
            "p95_s": self.p95,
            "max_s": self.maximum,
            "verdict": self.verdict.value,
            "note": self.budget.note,
        }


def percentile(values: Sequence[float], q: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _verdict(budget: Budget, values: Sequence[float], min_samples: int) -> Verdict:
    if budget.tripwire:
        return Verdict.FAIL if values and max(values) > budget.seconds else Verdict.PASS
    if len(values) < min_samples:
        return Verdict.INSUFFICIENT
    p95 = percentile(values, 0.95)
    return Verdict.FAIL if p95 is not None and p95 > budget.seconds else Verdict.PASS


def _budget_values(
    grouped: Mapping[tuple[str, str | None], list[float]], budget: Budget
) -> list[float]:
    """Samples a budget judges: its phase, and its condition when it names one."""
    return [
        value
        for (phase, condition), group in grouped.items()
        if phase == budget.phase and budget.condition in (None, condition)
        for value in group
    ]


def check_budgets(
    samples: Iterable[PhaseSample],
    *,
    budgets: Sequence[Budget] = ACCEPTED_BUDGETS,
    min_samples: int = MIN_SAMPLES,
    multi_step: bool = True,
) -> list[PhaseCheck]:
    """One check per budget that has samples; a budgeted phase with none is MISSING.

    ``multi_step`` is False when no checked trial ran two or more
    checkpoints; between-step budgets are then NOT_APPLICABLE.
    """
    grouped: dict[tuple[str, str | None], list[float]] = defaultdict(list)
    phases_seen: set[str] = set()
    for sample in samples:
        grouped[(sample.phase, sample.condition)].append(sample.seconds)
        phases_seen.add(sample.phase)
    checks = []
    missing: set[str] = set()
    for budget in budgets:
        values = _budget_values(grouped, budget)
        if not values:
            if budget.multi_step_only and not multi_step:
                checks.append(PhaseCheck(budget, 0, None, None, None, Verdict.NOT_APPLICABLE))
            elif budget.phase not in phases_seen | missing and not budget.tripwire:
                missing.add(budget.phase)
                checks.append(PhaseCheck(budget, 0, None, None, None, Verdict.MISSING))
            continue
        checks.append(
            PhaseCheck(
                budget,
                len(values),
                percentile(values, 0.5),
                percentile(values, 0.95),
                max(values),
                _verdict(budget, values, min_samples),
            )
        )
    return checks


class PerfInputError(ValueError):
    """A trial record or job manifest that cannot be read: malformed input, not a breach."""


@dataclass(frozen=True)
class TrialSamples:
    """One trial's phase samples, keyed by its job directory and trial id."""

    run: str
    trial: str
    trial_dir: Path
    samples: tuple[PhaseSample, ...]


@dataclass(frozen=True)
class JobSamples:
    samples: list[PhaseSample]
    trial_count: int
    multi_step: bool  # some trial ran two or more checkpoints
    trials: tuple[TrialSamples, ...] = ()


def _read_record(path: Path) -> TrialRecord:
    try:
        return TrialRecord.model_validate_json(path.read_text())
    except (OSError, ValidationError) as error:
        raise PerfInputError(f"{path}: not a readable trial record ({error})") from error


def load_job_samples(job_dirs: Sequence[Path]) -> JobSamples:
    """Every phase sample recorded under the jobs' trial records."""
    samples: list[PhaseSample] = []
    trials: list[TrialSamples] = []
    multi_step = False
    for job_dir in job_dirs:
        if is_legacy_job(job_dir):
            raise PerfInputError(legacy_job_message(job_dir))
        for path in sorted(job_dir.glob(TRIAL_RECORD_GLOB)):
            record = _read_record(path)
            samples.extend(record.phases)
            trials.append(
                TrialSamples(
                    str(job_dir), record.trial.trial_id, job_dir / record.trial_dir, record.phases
                )
            )
            multi_step = multi_step or len(record.trial.steps) >= 2
    return JobSamples(samples, len(trials), multi_step, tuple(trials))


def checks_pass(checks: Sequence[PhaseCheck]) -> bool:
    return all(check.verdict in PASSING for check in checks)
