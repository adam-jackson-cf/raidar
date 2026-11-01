"""One step's ``gate/1`` record read for ``verification-effort``: its values.

Statuses, never a zero for a missing value:

- in a gated trajectory, a reached step without a ``gate/1`` record is ``unavailable``, and an
  unreached checkpoint is ``not_applicable``;
- a step with no seal, or whose last seal could not decide, has ``insufficient_evidence`` for what
  no seal decided;
- a step that changed no production or test file is ``not_applicable`` to first-pass cleanliness
  and the first seal's blocking count (the first seal checked nothing new).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.verification_effort.grades import Grades, correctness_values
from raidar.scoring.verification_effort.measured import Measured, flag, same_for
from raidar.scoring.verification_effort.metrics import (
    BLOCKING_CHECKS,
    CLEAN_AT_END,
    FIRST_PASS_CLEAN,
    FIRST_RUN_BLOCKING,
    FIX_ROUNDS,
    GATE_ERROR,
    GATE_WALL,
    ROUNDS_TO_CLEAN,
    STEP_IDS,
    UNRESOLVED,
)
from raidar.scoring.verification_effort.records import (
    GateRecordError,
    StepGate,
    read_step_gate,
)
from raidar.scoring.verification_effort.sources import NO_SOURCE_CHANGE

_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
NO_EDIT_NA = (FIRST_PASS_CLEAN, FIRST_RUN_BLOCKING)


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


def _undecided(gate: StepGate) -> str:
    last = gate.last
    if last is None:
        return "the step ended before any gate seal"
    return f"the last gate seal (run {last.run}) could not decide: {last.error or last.verdict}"


def _first_pass(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, _undecided(gate))
    first = gate.runs[0]
    if not first.decided:
        return Measured.gap(_INSUFFICIENT, f"the first gate seal could not decide: {first.error}")
    return flag(first.passed)


def _clean_at_end(gate: StepGate) -> Measured:
    clean = gate.clean_at_end
    return Measured.gap(_INSUFFICIENT, _undecided(gate)) if clean is None else flag(clean)


def _unresolved(gate: StepGate) -> Measured:
    clean = gate.clean_at_end
    return Measured.gap(_INSUFFICIENT, _undecided(gate)) if clean is None else flag(not clean)


def _rounds_to_clean(gate: StepGate) -> Measured:
    clean = gate.clean_at_end
    if clean is None:
        return Measured.gap(_INSUFFICIENT, _undecided(gate))
    if not clean:
        return Measured.gap(
            _NA, "the gate never passed in this step (censored; counted by unresolved)"
        )
    return Measured(float(len(gate.fixes)))


def _gate_error(gate: StepGate) -> Measured:
    if not gate.runs:
        return Measured.gap(_INSUFFICIENT, _undecided(gate))
    return flag(any(not run.decided for run in gate.runs))


def _first_blocking(gate: StepGate) -> Measured:
    if not gate.runs or not gate.runs[0].decided:
        return Measured.gap(_INSUFFICIENT, "the first gate seal did not decide")
    return Measured(float(len(gate.runs[0].blocking_failures)))


def _blocking(gate: StepGate) -> Measured:
    decided = [run for run in gate.runs if run.decided]
    if not decided:
        return Measured.gap(_INSUFFICIENT, "no gate seal decided")
    return Measured(float(sum(len(run.blocking_failures) for run in decided)))


def _sum(values: Sequence[float | None], missing: str) -> Measured:
    if any(value is None for value in values):
        return Measured.gap(_UNAVAILABLE, missing)
    return Measured(float(sum(value or 0.0 for value in values)))


def _usage(gate: StepGate) -> dict[str, Measured]:
    fixes = gate.fixes
    unreported = "a fix turn did not report {}"
    values = {
        "fix_input_tokens": _sum(
            [turn.input_tokens for turn in fixes], unreported.format("input tokens")
        ),
        "fix_output_tokens": _sum(
            [turn.output_tokens for turn in fixes], unreported.format("output tokens")
        ),
        "fix_cost_usd": _sum([turn.cost_usd for turn in fixes], unreported.format("a cost")),
        "fix_wall_s": _sum([turn.seconds for turn in fixes], unreported.format("a duration")),
    }
    values[GATE_WALL] = _sum(
        [run.seconds for run in gate.runs], "the gate record has no seal timing"
    )
    return values


def _step_values(step: Step, gate: StepGate, grades: Grades) -> dict[str, Measured]:
    return (
        {
            "gate_runs": Measured(float(len(gate.runs))),
            FIX_ROUNDS: Measured(float(len(gate.fixes))),
            FIRST_PASS_CLEAN: _first_pass(gate),
            CLEAN_AT_END: _clean_at_end(gate),
            UNRESOLVED: _unresolved(gate),
            GATE_ERROR: _gate_error(gate),
            ROUNDS_TO_CLEAN: _rounds_to_clean(gate),
            FIRST_RUN_BLOCKING: _first_blocking(gate),
            BLOCKING_CHECKS: _blocking(gate),
        }
        | _usage(gate)
        | correctness_values(step.inputs, gate, grades)
    )


def step_values(step: Step, unchanged: frozenset[str], grades: Grades) -> dict[str, Measured]:
    """Every checkpoint metric of one step."""
    if not step.inputs.reached:
        return same_for(STEP_IDS, Measured.gap(_NA, "checkpoint not reached"))
    if step.error is not None:
        return same_for(STEP_IDS, Measured.gap(_UNAVAILABLE, step.error))
    if step.gate is None:
        reason = "no gate/1 record for this step, though other steps have one"
        return same_for(STEP_IDS, Measured.gap(_UNAVAILABLE, reason))
    values = _step_values(step, step.gate, grades)
    if step.inputs.step in unchanged:
        gap = Measured.not_applicable(NO_SOURCE_CHANGE)
        values |= {name: gap for name in NO_EDIT_NA if values[name].ok}
    return values
