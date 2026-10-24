"""A step's blocking findings, in total and by rule family.

A failed tests check counts once: the gate reports one ``TEST.failed`` finding per failing test
(capped at 20), while a missed coverage minimum or missing tests count once, so every blocking
count counts a run's ``TEST.failed`` findings as one finding. A run whose stored files do not
list every blocking code cannot be counted that way: its counts are ``unavailable``. The
``typing`` family counts ``MYPY.no-untyped-call`` call sites, so the cascade is reported beside it
(``first_run_untyped_call``, ``step_end_untyped_call``).
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.families import FAMILIES, family_of
from raidar.scoring.gate_effort.measured import Measured, same_for
from raidar.scoring.gate_effort.metrics import (
    BLOCKING_FINDINGS,
    FIRST_RUN_BLOCKING,
    FIRST_RUN_UNTYPED_CALL,
    STEP_END_BLOCKING,
    STEP_END_UNTYPED_CALL,
)
from raidar.scoring.gate_effort.outcomes import first_pass, incomplete
from raidar.scoring.gate_effort.records import GateRunView, StepGate
from raidar.scoring.gate_effort.tests_check import FAILED_CODE

_OK = AssessStatus.OK
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE
UNTYPED_CALL_CODE = "MYPY.no-untyped-call"
CODES_UNLISTED = (
    "the run's blocking codes are not all listed, so a failed tests check cannot be counted once"
)


def _family_counts(codes: Sequence[str]) -> dict[str, int]:
    counted = dict.fromkeys(FAMILIES, 0)
    for code in codes:
        counted[family_of(code)] += 1
    return counted


def codes_once(run: GateRunView) -> tuple[str, ...] | None:
    """The run's blocking codes with its ``TEST.failed`` findings as one (None: unlisted)."""
    if run.findings.codes is None:
        return () if run.findings.total == 0 else None
    seen_failed = False
    kept: list[str] = []
    for code in run.findings.codes:
        if code == FAILED_CODE:
            if seen_failed:
                continue
            seen_failed = True
        kept.append(code)
    return tuple(kept)


def _listed_codes(runs: Sequence[GateRunView]) -> list[str] | str:
    """Every run's blocking codes, a failed tests check once per run; or why they are unknown."""
    unknown = next((run for run in runs if run.findings.total is None), None)
    if unknown is not None:
        return incomplete(unknown)
    listed = [codes_once(run) for run in runs]
    if any(codes is None for codes in listed):
        return CODES_UNLISTED
    return [code for found in listed for code in found or ()]


def _counted(prefix: str, runs: Sequence[GateRunView]) -> dict[str, Measured]:
    """Total and families over ``runs``, a failed tests check counted once per run."""
    names = [prefix, *(f"{prefix}.{family}" for family in FAMILIES)]
    codes = _listed_codes(runs)
    if isinstance(codes, str):
        return same_for(names, Measured.gap(_UNAVAILABLE, codes))
    counts = _family_counts(codes)
    return {prefix: Measured(float(len(codes)))} | {
        f"{prefix}.{family}": Measured(float(counts[family])) for family in FAMILIES
    }


def _untyped_calls(run: GateRunView) -> Measured:
    if run.findings.total == 0:
        return Measured(0.0)
    if run.findings.codes is None:
        return Measured.gap(_UNAVAILABLE, incomplete(run))
    return Measured(float(sum(code == UNTYPED_CALL_CODE for code in run.findings.codes)))


def finding_values(gate: StepGate) -> dict[str, Measured]:
    """The step's blocking counts: first run, all decided runs and the step end."""
    first_names = [FIRST_RUN_BLOCKING, *(f"{FIRST_RUN_BLOCKING}.{f}" for f in FAMILIES)]
    first = first_pass(gate)
    if first.status is not _OK:
        gap = Measured.gap(first.status, first.reason or "")
        values = same_for([*first_names, FIRST_RUN_UNTYPED_CALL], gap)
    else:
        values = _counted(FIRST_RUN_BLOCKING, gate.runs[:1])
        values[FIRST_RUN_UNTYPED_CALL] = _untyped_calls(gate.runs[0])
    decided = [run for run in gate.runs if run.decided]
    if not decided:
        names = [BLOCKING_FINDINGS, *(f"{BLOCKING_FINDINGS}.{f}" for f in FAMILIES)]
        gap = Measured.gap(_INSUFFICIENT, first.reason or "no run decided")
        return values | same_for([*names, STEP_END_BLOCKING, STEP_END_UNTYPED_CALL], gap)
    last = decided[-1]
    return (
        values
        | _counted(BLOCKING_FINDINGS, decided)
        | {STEP_END_BLOCKING: _counted(STEP_END_BLOCKING, [last])[STEP_END_BLOCKING]}
        | {STEP_END_UNTYPED_CALL: _untyped_calls(last)}
    )
