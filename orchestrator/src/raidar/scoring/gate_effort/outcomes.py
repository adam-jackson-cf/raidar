"""One step's gate record read for ``gate-effort``: its outcomes and the fix sessions' usage.

Statuses, never a zero for a missing value:

- in a gated trajectory, a reached step without a record is ``unavailable``, and an
  unreached checkpoint is ``not_applicable``;
- a step whose session failed before any gate run, or whose gate errored, has
  ``insufficient_evidence`` for what no run decided.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.measured import Measured, flag
from raidar.scoring.gate_effort.records import (
    GateRecordError,
    GateRunView,
    StepGate,
    read_step_gate,
)

_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE


@dataclass(frozen=True)
class Step:
    inputs: StepInputs
    gate: StepGate | None
    error: str | None = None

    @property
    def gated(self) -> bool:
        return self.gate is not None or self.error is not None


def read_step(inputs: StepInputs) -> Step:
    if not inputs.reached:
        return Step(inputs, None)
    try:
        return Step(inputs, read_step_gate(inputs.trace_dir))
    except GateRecordError as exc:
        return Step(inputs, None, str(exc))


def _ended_early(gate: StepGate) -> str:
    detail = f": {gate.error}" if gate.error else ""
    return f"the step ended {gate.termination_reason} before the gate decided{detail}"


def first_pass(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, f"no gate run: {_ended_early(gate)}")
    first = gate.runs[0]
    if not first.decided:
        return Measured.gap(_INSUFFICIENT, f"the first gate run could not decide: {first.error}")
    return flag(first.passed)


def _unresolved(gate: StepGate) -> Measured:
    if gate.termination_reason == "rounds_exhausted":
        return flag(True)
    if gate.termination_reason == "gate_clean":
        return flag(False)
    return Measured.gap(_INSUFFICIENT, _ended_early(gate))


def _clean_at_end(gate: StepGate) -> Measured:
    if gate.clean_at_end is None:
        return Measured.gap(_INSUFFICIENT, _ended_early(gate))
    return flag(gate.clean_at_end)


def _rounds_to_clean(gate: StepGate) -> Measured:
    if gate.clean_at_end is None:
        return Measured.gap(_INSUFFICIENT, _ended_early(gate))
    if not gate.clean_at_end:
        return Measured.gap(
            _NA, "the gate never passed in this step (censored; counted by unresolved)"
        )
    return Measured(float(len(gate.fix_sessions)))


def _gate_error(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, f"no gate run: {_ended_early(gate)}")
    errored = gate.termination_reason == "gate_error" or any(not run.decided for run in gate.runs)
    return flag(errored)


def outcome_values(gate: StepGate) -> dict[str, Measured]:
    return {
        "gate_runs": Measured(float(len(gate.runs))),
        "fix_rounds": Measured(float(len(gate.fix_sessions))),
        "first_pass_clean": first_pass(gate),
        "gate_clean_at_step_end": _clean_at_end(gate),
        "unresolved": _unresolved(gate),
        "gate_error": _gate_error(gate),
        "rounds_to_clean": _rounds_to_clean(gate),
    }


def incomplete(run: GateRunView) -> str:
    findings = run.findings
    if findings.total is None:
        return (
            f"gate run {run.run}: the gate's output format is not recognised and it left no report"
        )
    return (
        f"gate run {run.run}: the gate listed {findings.listed} of {findings.total} blocking "
        "findings and left no report naming them all"
    )


def _sum_reported(values: Sequence[float | None], missing: str) -> Measured:
    if any(value is None for value in values):
        return Measured.gap(_UNAVAILABLE, missing)
    return Measured(float(sum(value or 0.0 for value in values)))


def usage_values(gate: StepGate) -> dict[str, Measured]:
    fixes = gate.fix_sessions
    unreported = "a fix session did not report {}"
    values = {
        f"fix_{metric}": _sum_reported(
            [session.usage[metric] for session in fixes], unreported.format(metric)
        )
        for metric in ("input_tokens", "output_tokens", "cost_usd")
    }
    values["fix_wall_s"] = _sum_reported(
        [session.duration_s for session in fixes], "the gate record has no fix-session timing"
    )
    values["gate_wall_s"] = _sum_reported(
        [run.duration_s for run in gate.runs], "the gate record has no gate-run timing"
    )
    return values
