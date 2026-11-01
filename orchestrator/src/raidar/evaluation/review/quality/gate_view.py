"""A trial's verification effort for the Quality run view (``verification-effort`` + gate/1).

Step cells (rounds to clean, first-pass clean, unresolved, gate error, failed
blocking checks, fix-session cost and time) are the stored
``verification-effort`` Assessment's observations, so the view and the analysis
read one source. The per-round rows (each gate run's verdict, attribution,
duration and failing blocking checks) come from the step's ``gate/1`` record in
``workflow/workflow.json`` through the scorer's own reader
(``raidar.scoring.verification_effort.records``), so a round is read by the
same code that scored it.

A trial without any gate/1 record and without a measured Assessment has no
gate view: a trial that ran without the gate shows nothing rather than zeros.
A record that cannot be read is an explicit note on the view, never a zero.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from raidar.evaluation.review.quality.models import (
    Cell,
    CellStatus,
    GateRound,
    GateStep,
    GateView,
)
from raidar.evaluation.review.quality.sources import TrialSource
from raidar.scoring.catalog.outcomes import VERIFICATION_EFFORT_REF
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.framework.store import Assessment
from raidar.scoring.verification_effort.records import (
    GateRecordError,
    GateRunView,
    StepGate,
    read_step_gate,
)

STEP_CELLS = (
    "gate_runs",
    "fix_rounds",
    "first_pass_clean",
    "gate_clean_at_step_end",
    "unresolved",
    "gate_error",
    "rounds_to_clean",
    "first_run_blocking",
    "blocking_checks",
    "fix_input_tokens",
    "fix_output_tokens",
    "fix_cost_usd",
    "fix_wall_s",
    "gate_wall_s",
)
TOTAL_CELLS = (
    "first_pass_clean_rate",
    "clean_at_step_end_rate",
    "unresolved_rate",
    "gate_error_rate",
    "mean_rounds_to_clean",
    "total_fix_rounds",
    "total_first_run_blocking",
    "total_blocking_checks",
    "total_fix_cost_usd",
    "total_fix_wall_s",
    "total_gate_wall_s",
)
_UNASSESSED = "no verification-effort assessment matches this trial"


class _Cells:
    def __init__(self, assessment: Assessment | None) -> None:
        self._items: dict[tuple[str, str], Observation] = {}
        if assessment is not None:
            for item in assessment.result.observations:
                self._items[(item.metric, item.subject)] = item
        self.present = assessment is not None

    def cell(self, metric: str, subject: str) -> Cell:
        item = self._items.get((metric, subject))
        if item is None:
            return Cell(
                status="unassessed" if not self.present else "unavailable", reason=_UNASSESSED
            )
        if item.status is AssessStatus.OK:
            return Cell(status="ok", value=item.value)
        status = cast(CellStatus, item.status.value)
        return Cell(status=status, reason=item.reason or item.status.value)

    def measured(self) -> bool:
        return any(item.status is AssessStatus.OK for item in self._items.values())


def _round(run: GateRunView) -> GateRound:
    return GateRound(
        run=run.run,
        after=run.after,
        verdict=run.verdict,
        attribution=run.attribution,
        seconds=run.seconds,
        error=run.error,
        failed_checks=tuple(
            check.name for check in run.checks if check.blocking and check.status != "passed"
        ),
    )


def _trace_dirs(
    trial: TrialSource, checkpoints: Sequence[str], notes: list[str]
) -> dict[str, Path | None]:
    """Each step's agent dir, which must stay inside the trial directory."""
    recorded = {
        result.step: result.trace_dir for result in trial.record.step_results if result.trace_dir
    }
    root = trial.trial_dir.resolve()
    found: dict[str, Path | None] = {}
    for checkpoint in checkpoints:
        path = trial.trial_dir / (recorded.get(checkpoint) or f"steps/{checkpoint}/agent")
        if path.resolve().is_relative_to(root):
            found[checkpoint] = path
        else:
            notes.append(f"{checkpoint}: step directory is outside the trial; gate record skipped")
            found[checkpoint] = None
    return found


GateReader = Callable[[Path], StepGate | None]
"""Reads one step's ``gate/1`` record from its agent dir."""


def read_gate_from_disk(trace_dir: Path) -> StepGate | None:
    return read_step_gate(trace_dir if trace_dir.is_dir() else None)


def _read(
    trace_dir: Path | None, notes: list[str], checkpoint: str, reader: GateReader
) -> StepGate | None:
    if trace_dir is None:
        return None
    try:
        return reader(trace_dir)
    except GateRecordError as exc:
        notes.append(f"{checkpoint}: gate record unreadable ({exc})")
        return None


def _step(checkpoint: str, gate: StepGate | None, cells: _Cells) -> GateStep:
    return GateStep(
        checkpoint=checkpoint,
        cells={metric: cells.cell(metric, checkpoint) for metric in STEP_CELLS},
        rounds=tuple(_round(run) for run in gate.runs) if gate else (),
        errors=tuple(gate.errors) if gate else (),
    )


def _assessment_notes(assessment: Assessment | None) -> list[str]:
    """Why the cells are missing."""
    if assessment is None:
        return [f"{_UNASSESSED}: rescore its job with {VERIFICATION_EFFORT_REF}"]
    return []


def build_gate_view(
    trial: TrialSource,
    assessment: Assessment | None,
    checkpoints: Sequence[str],
    reader: GateReader = read_gate_from_disk,
) -> GateView | None:
    """The trial's verification effort, or None when it ran without a gate/1 record."""
    cells = _Cells(assessment)
    notes: list[str] = []
    traces = _trace_dirs(trial, checkpoints, notes)
    gates = {
        checkpoint: _read(traces[checkpoint], notes, checkpoint, reader)
        for checkpoint in checkpoints
    }
    if not cells.measured() and not notes and all(gate is None for gate in gates.values()):
        return None
    notes.extend(_assessment_notes(assessment))
    return GateView(
        source=assessment.key if assessment is not None else None,
        scorer=assessment.scorer.ref if assessment is not None else None,
        steps=tuple(_step(checkpoint, gates[checkpoint], cells) for checkpoint in checkpoints),
        totals={metric: cells.cell(metric, TRAJECTORY_SUBJECT) for metric in TOTAL_CELLS},
        notes=tuple(notes),
    )
