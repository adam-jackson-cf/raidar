"""The audit's shared inputs: the check constructor and what one trial left behind."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from raidar.contract.harbor_result import RESULT_FILE, read_result, step_results
from raidar.contract.trial import NOT_STARTED, CheckpointState, StepResult, TrialRecord
from raidar.engine.health.model import HealthCheck, HealthFamily, HealthStatus
from raidar.engine.health.reaper import REAP_REPORT
from raidar.grading.records import (
    PRE_AGENT_LEAK_FILE,
    RECORD_FILE,
    StepRecord,
    load_step_record,
    step_record_dir,
)


def make_check(check_id: str, status: HealthStatus, detail: str, **evidence: Any) -> HealthCheck:
    return HealthCheck(
        id=check_id,
        family=cast(HealthFamily, check_id[0]),
        status=status,
        detail=detail,
        evidence=evidence,
    )


def attempted_steps(record: TrialRecord) -> tuple[StepResult, ...]:
    """The steps that started: the ones a trial's audit holds to its record files."""
    return tuple(
        r
        for r in record.step_results
        if r.checkpoint_state is not CheckpointState.NOT_ATTEMPTED and r.termination != NOT_STARTED
    )


def read_ledger(trial_dir: Path, step: str) -> StepRecord | None:
    """A step's verifier ledger, or None when it is absent or unreadable."""
    record_dir = step_record_dir(trial_dir, step)
    if not (record_dir / RECORD_FILE).is_file():
        return None
    try:
        return load_step_record(record_dir)
    except ValueError:
        return None


class Audit:
    """What one trial left behind, read once: the record, the job manifest, Harbor's result and
    the step ledgers (``None`` for a step the verifier never graded)."""

    def __init__(
        self,
        job_dir: Path,
        trial_dir: Path,
        record: TrialRecord,
        job: Mapping[str, Any],
        *,
        result: Mapping[str, Any] | None,
        ledgers: Mapping[str, StepRecord | None],
    ) -> None:
        self.job_dir = job_dir
        self.trial_dir = trial_dir
        self.record = record
        self.job = job
        self.result = result
        self.items = step_results(result)
        self.attempted = attempted_steps(record)
        self._ledgers = ledgers

    @classmethod
    def from_disk(
        cls, job_dir: Path, trial_dir: Path, record: TrialRecord, job: Mapping[str, Any]
    ) -> Audit:
        """Read the trial's ``result.json`` and every attempted step's ledger from ``trial_dir``."""
        return cls(
            job_dir,
            trial_dir,
            record,
            job,
            result=read_result(trial_dir),
            ledgers={r.step: read_ledger(trial_dir, r.step) for r in attempted_steps(record)},
        )

    def ledger(self, step: str) -> StepRecord | None:
        return self._ledgers.get(step)

    def graded(self) -> list[tuple[StepResult, StepRecord]]:
        pairs = ((r, self.ledger(r.step)) for r in self.attempted)
        return [(r, ledger) for r, ledger in pairs if ledger is not None]

    def expected_files(self) -> list[tuple[str, Path]]:
        """Every record file an executed trial must hold, as (label, path)."""
        expected = [(RESULT_FILE, self.trial_dir / RESULT_FILE)]
        for step in self.attempted:
            record_dir = step_record_dir(self.trial_dir, step.step)
            names = (RECORD_FILE, REAP_REPORT, PRE_AGENT_LEAK_FILE)
            expected += [(f"{step.step}/{name}", record_dir / name) for name in names]
        return expected

    @property
    def arm(self) -> str:
        return self.record.trial.arm

    @property
    def limits(self) -> Mapping[str, Any] | None:
        limits = self.job.get("effective_limits")
        return limits if isinstance(limits, Mapping) else None
