"""Each fix session's cost and wall time, by the refusal that started it.

A guardrail-gate loop runs fix session ``r`` (its ``round``) right after gate
run ``r`` refused, so the session's refusal is that run's blocking codes (a
failed tests check once). Its kind (``REFUSAL_KINDS``) is the one
rule family of those codes (``TEST.*`` is ``tests``), ``coverage_minimum``
when ``TEST.coverage-minimum`` was the only code, ``mixed`` for more than one
family, or ``unknown`` when the codes are not all listed (or none were). The
kinds partition the step's fix sessions, so the split adds up to
the step's ``fix_cost_usd`` and ``fix_wall_s``. Beside the split, the sessions whose
refusal included ``TEST.coverage-minimum`` at all (any other code with it):
whether the coverage minimum drives the fix cost.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.autofix_metrics import (
    COVERAGE_COST,
    COVERAGE_ONLY,
    COVERAGE_SESSIONS,
    COVERAGE_WALL,
    FIX_COST_BY_REFUSAL,
    FIX_WALL_BY_REFUSAL,
    MIXED,
    REFUSAL_KINDS,
    UNKNOWN,
)
from raidar.scorers.gate_effort.families import family_of
from raidar.scorers.gate_effort.findings import codes_once
from raidar.scorers.gate_effort.measured import Measured
from raidar.scorers.gate_effort.records import GateRunView, SessionView, StepGate
from raidar.scorers.gate_effort.tests_check import COVERAGE_CODE
from raidar.scorers.sdk.result import AssessStatus

_UNAVAILABLE = AssessStatus.UNAVAILABLE

SPLIT_METRICS = (
    *(f"{FIX_COST_BY_REFUSAL}.{kind}" for kind in REFUSAL_KINDS),
    *(f"{FIX_WALL_BY_REFUSAL}.{kind}" for kind in REFUSAL_KINDS),
    COVERAGE_SESSIONS,
    COVERAGE_COST,
    COVERAGE_WALL,
)


def refusal_codes(gate: StepGate, session: SessionView) -> tuple[str, ...] | None:
    """The blocking codes of the gate run that started a fix session (None: unknown)."""
    if not 0 < session.round <= len(gate.runs):
        return None
    run: GateRunView = gate.runs[session.round - 1]
    return codes_once(run)


def refusal_kind(codes: tuple[str, ...] | None) -> str:
    if not codes:
        return UNKNOWN
    if all(code == COVERAGE_CODE for code in codes):
        return COVERAGE_ONLY
    families = {family_of(code) for code in codes}
    return families.pop() if len(families) == 1 else MIXED


def _gap_or(values: list[float | None], missing: str) -> Measured:
    if any(value is None for value in values):
        return Measured.gap(_UNAVAILABLE, missing)
    return Measured(float(sum(value or 0.0 for value in values)))


def _split(
    sessions: list[tuple[SessionView, str]], prefix: str, measure: str
) -> dict[str, Measured]:
    missing = f"a fix session did not report {measure}"
    found = {}
    for kind in REFUSAL_KINDS:
        picked = [_measure(session, measure) for session, k in sessions if k == kind]
        found[f"{prefix}.{kind}"] = _gap_or(picked, missing)
    return found


def _measure(session: SessionView, measure: str) -> float | None:
    return session.duration_s if measure == "wall" else session.usage.get("cost_usd")


def _coverage(gate: StepGate, sessions: list[SessionView]) -> dict[str, Measured]:
    codes = [refusal_codes(gate, session) for session in sessions]
    if any(found is None for found in codes):
        gap = Measured.gap(_UNAVAILABLE, "a fix session's refusal codes are not all listed")
        return dict.fromkeys((COVERAGE_SESSIONS, COVERAGE_COST, COVERAGE_WALL), gap)
    with_coverage = [
        s for s, found in zip(sessions, codes, strict=True) if COVERAGE_CODE in (found or ())
    ]
    return {
        COVERAGE_SESSIONS: Measured(float(len(with_coverage))),
        COVERAGE_COST: _gap_or(
            [_measure(s, "cost") for s in with_coverage], "a fix session did not report cost"
        ),
        COVERAGE_WALL: _gap_or(
            [_measure(s, "wall") for s in with_coverage], "a fix session did not report wall time"
        ),
    }


def refusal_values(gate: StepGate) -> dict[str, Measured]:
    """The step's fix-session cost and wall time by refusal kind, and the coverage share."""
    sessions = list(gate.fix_sessions)
    kinds = [(session, refusal_kind(refusal_codes(gate, session))) for session in sessions]
    return (
        _split(kinds, FIX_COST_BY_REFUSAL, "cost")
        | _split(kinds, FIX_WALL_BY_REFUSAL, "wall")
        | _coverage(gate, sessions)
    )


__all__ = ["SPLIT_METRICS", "refusal_codes", "refusal_kind", "refusal_values"]
