"""Per-step values that compare across gate modes: first-pass variants and cascade-free counts.

A shadow gate checks the whole tree at every step and never fixes anything, so the counts
here take the typing cascade out (``MYPY.no-untyped-call``, reported once per call site of an
untyped ``def``) and fold a refused suppression comment's ``MYPY.unused-ignore`` into its
``SUPPRESS`` (``folding``). A run whose folded findings cannot be located from the stored
files makes the step's net count ``unavailable`` (never a guess).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.cross_mode.metrics import (
    AUTOFIX_CODES,
    EX_CASCADE_STEP_METRICS,
    FIRST_PASS_EXCEPT_AUTOFIX,
    FIRST_PASS_EXCEPT_BOTH,
    FIRST_PASS_EXCEPT_TESTS,
    FIRST_RUN_EX_CASCADE,
    FIRST_RUN_NET,
    NET_STEP_METRICS,
    NEW_EX_CASCADE,
    NEW_NET,
    STEP_END_EX_CASCADE,
    STEP_END_NET,
)
from raidar.scoring.gate_effort.families import TEST_CODE_PREFIX
from raidar.scoring.gate_effort.findings import CODES_UNLISTED, codes_once
from raidar.scoring.gate_effort.folding import duplicates, raw_runs
from raidar.scoring.gate_effort.measured import Measured
from raidar.scoring.gate_effort.metrics import (
    FIRST_RUN_BLOCKING,
    FIRST_RUN_UNTYPED_CALL,
    STEP_END_BLOCKING,
    STEP_END_UNTYPED_CALL,
)
from raidar.scoring.gate_effort.outcomes import Step, first_pass
from raidar.scoring.gate_effort.records import StepGate

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE

LOCATIONS_UNKNOWN = "the run's folded findings cannot be located from the stored files"


def _autofix(code: str) -> bool:
    return code in AUTOFIX_CODES


def _tests(code: str) -> bool:
    return code.startswith(TEST_CODE_PREFIX)


def _either(code: str) -> bool:
    return _autofix(code) or _tests(code)


FIRST_PASS_VARIANTS: dict[str, Callable[[str], bool]] = {
    FIRST_PASS_EXCEPT_AUTOFIX: _autofix,
    FIRST_PASS_EXCEPT_TESTS: _tests,
    FIRST_PASS_EXCEPT_BOTH: _either,
}
"""``{metric: ignored code}``: the first run is clean but for those codes."""


def first_pass_except(gate: StepGate, ignored: Callable[[str], bool]) -> Measured:
    """1 when the first run decided and every blocking code it gave is ``ignored``."""
    first = first_pass(gate)
    if first.status is not _OK:
        return first
    codes = codes_once(gate.runs[0])
    if codes is None:
        return Measured.gap(_UNAVAILABLE, CODES_UNLISTED)
    return Measured(1.0 if all(ignored(code) for code in codes) else 0.0)


def _minus(total: Measured, part: Measured) -> Measured:
    """``total - part``, or the first one's gap."""
    for measured in (total, part):
        if measured.status is not _OK or measured.value is None:
            return Measured.gap(measured.status, measured.reason or "not measured")
    return Measured((total.value or 0.0) - (part.value or 0.0))


def ex_cascade_values(step: Step, values: Mapping[str, Measured]) -> dict[str, Measured]:
    """The per-step additions from the raw values (``new_*`` filled in over the trajectory)."""
    names = [metric.id for metric in EX_CASCADE_STEP_METRICS]
    if not step.inputs.reached:
        return dict.fromkeys(names, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        # The raw values already carry every step metric's status (an error, or no record).
        first = values[FIRST_RUN_BLOCKING]
        return dict.fromkeys(names, Measured.gap(first.status, first.reason or "no gate record"))
    found = {
        name: first_pass_except(step.gate, ignored) for name, ignored in FIRST_PASS_VARIANTS.items()
    }
    found[FIRST_RUN_EX_CASCADE] = _minus(values[FIRST_RUN_BLOCKING], values[FIRST_RUN_UNTYPED_CALL])
    found[STEP_END_EX_CASCADE] = _minus(values[STEP_END_BLOCKING], values[STEP_END_UNTYPED_CALL])
    found[NEW_EX_CASCADE] = Measured.gap(_UNAVAILABLE, "not computed")
    return found


def _folded(step: Step, gate: StepGate, index: int) -> Measured:
    """The folded findings of the step's gate run at ``index``."""
    trace_dir = step.inputs.trace_dir
    recorded = raw_runs(trace_dir)
    if trace_dir is None or len(recorded) != len(gate.runs):
        return Measured.gap(_UNAVAILABLE, LOCATIONS_UNKNOWN)
    found = duplicates(trace_dir, recorded[index], gate.runs[index].findings.codes)
    if found is None:
        return Measured.gap(_UNAVAILABLE, LOCATIONS_UNKNOWN)
    return Measured(float(found))


def _net(step: Step, gate: StepGate, count: Measured, index: int | None) -> Measured:
    """``count`` (an ``*_ex_cascade`` value) minus the folded findings of run ``index``."""
    if count.status is not _OK or index is None or not gate.runs:
        return count
    return _minus(count, _folded(step, gate, index))


def net_values(step: Step, values: Mapping[str, Measured]) -> dict[str, Measured]:
    """The per-step ``*_net`` additions from the cascade-free values (``new_*`` filled in later)."""
    names = [metric.id for metric in NET_STEP_METRICS]
    if not step.inputs.reached:
        return dict.fromkeys(names, Measured.gap(_NA, "checkpoint not reached"))
    if step.gate is None or step.error is not None:
        first = values[FIRST_RUN_BLOCKING]
        return dict.fromkeys(names, Measured.gap(first.status, first.reason or "no gate record"))
    decided = [index for index, run in enumerate(step.gate.runs) if run.decided]
    return {
        NEW_NET: Measured.gap(_UNAVAILABLE, "not computed"),
        FIRST_RUN_NET: _net(step, step.gate, values[FIRST_RUN_EX_CASCADE], 0),
        STEP_END_NET: _net(
            step, step.gate, values[STEP_END_EX_CASCADE], decided[-1] if decided else None
        ),
    }
