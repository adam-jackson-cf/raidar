"""The audit's shared inputs: the check constructor and what one trial left behind."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from raidar.engine.health.model import HealthCheck, HealthFamily, HealthStatus
from raidar.engine.trials.collect import NOT_STARTED, RESULT_FILE, read_result, step_results
from raidar.engine.trials.models import CheckpointState, StepResult, TrialRecord
from raidar.engine.trials.reaper import REAP_REPORT
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


class Audit:
    """What one trial left behind, read once: the record, the job manifest and the step ledgers."""

    def __init__(
        self, job_dir: Path, trial_dir: Path, record: TrialRecord, job: Mapping[str, Any]
    ) -> None:
        self.job_dir = job_dir
        self.trial_dir = trial_dir
        self.record = record
        self.job = job
        self.result = read_result(trial_dir)
        self.items = step_results(self.result)
        self.attempted: tuple[StepResult, ...] = tuple(
            r
            for r in record.step_results
            if r.checkpoint_state is not CheckpointState.NOT_ATTEMPTED
            and r.termination != NOT_STARTED
        )
        self._ledgers: dict[str, StepRecord | None] = {}

    def ledger(self, step: str) -> StepRecord | None:
        if step not in self._ledgers:
            record_dir = step_record_dir(self.trial_dir, step)
            try:
                loaded = (
                    load_step_record(record_dir) if (record_dir / RECORD_FILE).is_file() else None
                )
            except ValueError:
                loaded = None
            self._ledgers[step] = loaded
        return self._ledgers[step]

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
