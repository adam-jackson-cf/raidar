"""A trial's commit-gate effort for the Quality run view (``gate-effort@1`` + gate records).

Step cells (rounds to clean, first-pass clean, unresolved, gate error,
blocking findings in total and by rule family, fix-session cost and time) are
the stored ``gate-effort@1`` Assessment's observations, so the view and the
analysis read one source. The per-round rows (each gate run's verdict, its
blocking findings by family and its output log) come from the step's
``guardrail-gate`` record through the scorer's own reader
(``raidar.scorers.gate_effort.records``), so a round's families are counted by
the same code that scored them.

A trial without any gate record and without a measured Assessment has no
gate view: an ungated arm shows nothing rather than zeros.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from raidar.review_quality.models import (
    Cell,
    CellStatus,
    GateRound,
    GateStep,
    GateVerdict,
    GateView,
)
from raidar.review_quality.sources import TrialSource
from raidar.scorers.gate_effort.families import FAMILIES, family_counts
from raidar.scorers.gate_effort.records import (
    GateRecordError,
    GateRunView,
    StepGate,
    read_step_gate,
)
from raidar.scorers.gate_effort.scorer import run_summary
from raidar.scorers.gate_effort_v2.scorer import SHADOW_MODE, step_mode
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scorers.store import Assessment

GATE_EFFORT_REF = "gate-effort@1"
GATE_EFFORT_REFS = ("gate-effort@4", "gate-effort@3", "gate-effort@2", GATE_EFFORT_REF)
"""Read in preference order: a trial assessed with ``@2`` or later shows a shadow gate's cells."""
STEP_CELLS = (
    "gate_runs",
    "fix_rounds",
    "first_pass_clean",
    "gate_clean_at_step_end",
    "unresolved",
    "gate_error",
    "rounds_to_clean",
    "first_run_blocking",
    "blocking_findings",
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
    "total_blocking_findings",
    "total_fix_cost_usd",
    "total_fix_wall_s",
    "total_gate_wall_s",
)
_UNASSESSED = "no gate-effort assessment matches this trial"


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


def _verdict(run: GateRunView) -> GateVerdict:
    if not run.decided:
        return "error"
    if run.passed:
        return "passed"
    return "timed_out" if run.timed_out else "refused"


def _round(trial: TrialSource, trace_dir: Path, run: GateRunView) -> GateRound:
    findings = run.findings
    log = trace_dir / run.log if run.log else None
    return GateRound(
        run=run.run,
        after=run.after,
        verdict=_verdict(run),
        blocking=findings.total,
        listed=findings.listed,
        families=dict(family_counts(findings.codes)) if findings.codes is not None else None,
        source=findings.source,
        summary=run_summary(run),
        log_path=trial.portable_path(log) if log is not None and log.is_file() else None,
        duration_s=run.duration_s,
    )


def _trace_dirs(trial: TrialSource, checkpoints: Sequence[str]) -> dict[str, Path]:
    recorded = {
        result.step: result.trace_dir for result in trial.record.step_results if result.trace_dir
    }
    return {
        checkpoint: trial.trial_dir / (recorded.get(checkpoint) or f"steps/{checkpoint}/agent")
        for checkpoint in checkpoints
    }


def _read(trace_dir: Path, notes: list[str], checkpoint: str) -> StepGate | None:
    try:
        return read_step_gate(trace_dir if trace_dir.is_dir() else None)
    except GateRecordError as exc:
        notes.append(f"{checkpoint}: gate record unreadable ({exc})")
        return None


def _step(
    trial: TrialSource,
    checkpoint: str,
    trace_dir: Path,
    gate: StepGate | None,
    cells: _Cells,
) -> GateStep:
    return GateStep(
        checkpoint=checkpoint,
        termination=gate.termination_reason if gate is not None else None,
        cells={metric: cells.cell(metric, checkpoint) for metric in STEP_CELLS},
        families={
            family: cells.cell(f"blocking_findings.{family}", checkpoint) for family in FAMILIES
        },
        first_run_families={
            family: cells.cell(f"first_run_blocking.{family}", checkpoint) for family in FAMILIES
        },
        rounds=tuple(_round(trial, trace_dir, run) for run in gate.runs) if gate else (),
    )


def _is_shadow(traces: dict[str, Path], gates: dict[str, StepGate | None]) -> bool:
    """A step with a gate record names shadow mode (guardrail-gate@2)."""
    return any(
        step_mode(traces[checkpoint]) == SHADOW_MODE
        for checkpoint, gate in gates.items()
        if gate is not None
    )


def _assessment_notes(assessment: Assessment | None, shadow: bool) -> list[str]:
    """Why the cells are missing, or why they misread a shadow gate."""
    if assessment is None:
        return [
            f"{_UNASSESSED}: rescore its job with {GATE_EFFORT_REF} "
            f"(or {GATE_EFFORT_REFS[0]} when its lock declares it)"
        ]
    if shadow and assessment.scorer.ref == GATE_EFFORT_REF:
        return [
            f"shadow gate assessed with {GATE_EFFORT_REF}, which reads it as an enforced gate; "
            f"rescore with {GATE_EFFORT_REFS[-2]} or later (the version its lock declares)"
        ]
    return []


def build_gate_view(
    trial: TrialSource, assessment: Assessment | None, checkpoints: Sequence[str]
) -> GateView | None:
    """The trial's gate effort, or None when it ran without the commit gate."""
    cells = _Cells(assessment)
    notes: list[str] = []
    traces = _trace_dirs(trial, checkpoints)
    gates = {checkpoint: _read(traces[checkpoint], notes, checkpoint) for checkpoint in checkpoints}
    if not cells.measured() and not notes and all(gate is None for gate in gates.values()):
        return None
    shadow = _is_shadow(traces, gates)
    notes.extend(_assessment_notes(assessment, shadow))
    return GateView(
        source=assessment.key if assessment is not None else None,
        scorer=assessment.scorer.ref if assessment is not None else None,
        shadow=shadow,
        steps=tuple(
            _step(trial, checkpoint, traces[checkpoint], gates[checkpoint], cells)
            for checkpoint in checkpoints
        ),
        totals={metric: cells.cell(metric, TRAJECTORY_SUBJECT) for metric in TOTAL_CELLS},
        notes=tuple(notes),
    )
