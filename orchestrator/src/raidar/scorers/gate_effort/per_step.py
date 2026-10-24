"""Every step's values before the autofix additions, with the shadow and no-edit rules applied.

A shadow gate's fix side and the fix rounds' correctness are not applicable; a step with no
source change is not applicable to first-pass clean and the first-run tests outcomes.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.cross_mode import FIRST_PASS_VARIANTS, ex_cascade_values, net_values
from raidar.scorers.gate_effort.cross_mode_metrics import (
    FIRST_RUN_EX_CASCADE,
    FIRST_RUN_NET,
    NEW_EX_CASCADE,
    NEW_NET,
    STEP_END_EX_CASCADE,
    STEP_END_NET,
)
from raidar.scorers.gate_effort.grades import Grades
from raidar.scorers.gate_effort.measured import Measured
from raidar.scorers.gate_effort.metrics import (
    FIRST_RUN_BLOCKING,
    FIRST_RUN_COVERAGE_MET,
    FIRST_RUN_FAILED_TESTS,
    FIRST_RUN_TESTS_FAILED,
    NEW_FIRST_RUN_BLOCKING,
    STEP_END_BLOCKING,
)
from raidar.scorers.gate_effort.new_counts import PerStep, with_new
from raidar.scorers.gate_effort.outcomes import Step
from raidar.scorers.gate_effort.raw_step import raw_step_values
from raidar.scorers.gate_effort.shadow import SHADOW_STEP_NA, shadowed
from raidar.scorers.gate_effort.sources import NO_SOURCE_CHANGE

FIRST_PASS_CLEAN = "first_pass_clean"
SHADOW_CORRECTNESS_NA = ("pre_fix_pass_rate", "correctness_delta")
NO_EDIT_NA = (
    "first_run_tests_passed",
    "first_run_tests_missing",
    FIRST_RUN_TESTS_FAILED,
    FIRST_RUN_COVERAGE_MET,
    FIRST_RUN_FAILED_TESTS,
)


def adjusted(
    step: Step, values: dict[str, Measured], shadow: bool, unchanged: frozenset[str]
) -> dict[str, Measured]:
    """The shadow rule and the no-edit rule on the step's values."""
    if shadow:
        values = shadowed(values, (*SHADOW_STEP_NA, *SHADOW_CORRECTNESS_NA))
    if step.gate is not None and step.inputs.step in unchanged:
        gap = Measured.not_applicable(NO_SOURCE_CHANGE)
        values = values | {name: gap for name in (FIRST_PASS_CLEAN, *NO_EDIT_NA) if values[name].ok}
    return values


def _no_edit(step: Step, values: dict[str, Measured], unchanged: frozenset[str]) -> None:
    """A step with no source change is not applicable to the first-pass variants."""
    if step.gate is None or step.inputs.step not in unchanged:
        return
    for name in FIRST_PASS_VARIANTS:
        if values[name].ok:
            values[name] = Measured.not_applicable(NO_SOURCE_CHANGE)


def per_step_values(
    steps: list[Step], shadow: bool, unchanged: frozenset[str], grades: Grades
) -> PerStep:
    """The base, cascade-free and net values of every step, the rules applied."""
    raw = [(step, raw_step_values(step, grades)) for step in steps]
    with_new(raw, FIRST_RUN_BLOCKING, STEP_END_BLOCKING, NEW_FIRST_RUN_BLOCKING)
    extended = [(step, values | ex_cascade_values(step, values)) for step, values in raw]
    with_new(extended, FIRST_RUN_EX_CASCADE, STEP_END_EX_CASCADE, NEW_EX_CASCADE)
    netted = [(step, values | net_values(step, values)) for step, values in extended]
    with_new(netted, FIRST_RUN_NET, STEP_END_NET, NEW_NET)
    found: PerStep = []
    for step, values in netted:
        final = adjusted(step, values, shadow, unchanged)
        _no_edit(step, final, unchanged)
        found.append((step, final))
    return found
