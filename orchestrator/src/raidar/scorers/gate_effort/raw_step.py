"""One step's base values: outcomes, findings, usage, tests check and fix-round correctness."""

from __future__ import annotations

from raidar.scorers.gate_effort.findings import finding_values
from raidar.scorers.gate_effort.grades import Grades, correctness_values
from raidar.scorers.gate_effort.measured import Measured, same_for
from raidar.scorers.gate_effort.metrics import NEW_FIRST_RUN_BLOCKING, RAW_STEP_METRICS
from raidar.scorers.gate_effort.outcomes import Step, outcome_values, usage_values
from raidar.scorers.gate_effort.tests_check import read_record, tests_values
from raidar.scorers.sdk.result import AssessStatus

_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE


def raw_step_values(step: Step, grades: Grades) -> dict[str, Measured]:
    if not step.inputs.reached:
        return same_for(RAW_STEP_METRICS, Measured.gap(_NA, "checkpoint not reached"))
    if step.error is not None:
        return same_for(RAW_STEP_METRICS, Measured.gap(_UNAVAILABLE, step.error))
    if step.gate is None:
        reason = "no guardrail-gate record for this step, though other steps have one"
        return same_for(RAW_STEP_METRICS, Measured.gap(_UNAVAILABLE, reason))
    record = read_record(step.inputs)
    values = (
        outcome_values(step.gate)
        | finding_values(step.gate)
        | usage_values(step.gate)
        | tests_values(step.inputs.trace_dir, record)
        | correctness_values(step.inputs, record, grades)
    )
    # Filled in over the trajectory (it needs the previous gated step's end).
    values[NEW_FIRST_RUN_BLOCKING] = Measured.gap(_UNAVAILABLE, "not computed")
    return values
