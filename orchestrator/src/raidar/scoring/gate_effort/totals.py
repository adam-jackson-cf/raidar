"""``gate-effort``'s trajectory values: rates over decided steps and totals over the gated steps.

Rates count decided steps only (numerator and denominator are reported); totals sum the gated
steps and are ``unavailable`` when any gated step lacks the value. ``all_totals`` is every
trajectory value of one gated trajectory (``steps``: the gated steps only, each with its name
and values).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from raidar.scoring.framework.result import AssessStatus
from raidar.scoring.gate_effort.autofix_metrics import (
    AUTOFIX_SUMMED,
    FIRST_PASS_POST,
    NEW_METRICS,
    STEP_END_POST,
)
from raidar.scoring.gate_effort.cross_mode_metrics import (
    FINAL_STEP_END_EX_CASCADE,
    FINAL_STEP_END_NET,
    FIRST_PASS_VARIANT_WHAT,
    NEW_EX_CASCADE,
    NEW_NET,
    STEP_END_EX_CASCADE,
    STEP_END_NET,
    TOTAL_NEW_EX_CASCADE,
    TOTAL_NEW_NET,
)
from raidar.scoring.gate_effort.families import FAMILIES
from raidar.scoring.gate_effort.measured import Measured
from raidar.scoring.gate_effort.metrics import (
    BLOCKING_FINDINGS,
    FIRST_RUN_BLOCKING,
    FIRST_RUN_COVERAGE_MET,
    FIRST_RUN_FAILED_TESTS,
    FIRST_RUN_TESTS_FAILED,
    FIRST_RUN_UNTYPED_CALL,
    NEW_FIRST_RUN_BLOCKING,
    STEP_END_BLOCKING,
    STEP_END_UNTYPED_CALL,
)

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE
_INSUFFICIENT = AssessStatus.INSUFFICIENT_EVIDENCE

Gated = Sequence[tuple[str, Mapping[str, Measured]]]

RATES = {
    "first_pass_clean_rate": "first_pass_clean",
    "clean_at_step_end_rate": "gate_clean_at_step_end",
    "unresolved_rate": "unresolved",
    "gate_error_rate": "gate_error",
}
_SUMMED = (
    "fix_rounds",
    FIRST_RUN_BLOCKING,
    *(f"{FIRST_RUN_BLOCKING}.{family}" for family in FAMILIES),
    BLOCKING_FINDINGS,
    *(f"{BLOCKING_FINDINGS}.{family}" for family in FAMILIES),
    "fix_input_tokens",
    "fix_output_tokens",
    "fix_cost_usd",
    "fix_wall_s",
    "gate_wall_s",
)
TOTALS = {f"total_{metric}": metric for metric in _SUMMED}
"""``{trajectory id: step metric}`` of the base totals (the sum over gated steps)."""


def _rate(metric: str, steps: Sequence[Mapping[str, Measured]]) -> Measured:
    decided = [values[metric].value for values in steps if values[metric].status is _OK]
    if not decided:
        return Measured.gap(_INSUFFICIENT, f"no gated step decided {metric}")
    numerator = float(sum(value or 0.0 for value in decided))
    return Measured(numerator / len(decided), numerator=numerator, denominator=float(len(decided)))


def _mean_rounds(steps: Sequence[Mapping[str, Measured]]) -> Measured:
    clean = [
        values["rounds_to_clean"].value or 0.0
        for values in steps
        if values["rounds_to_clean"].status is _OK
    ]
    if not clean:
        return Measured.gap(_NA, "no gated step ended with the gate passing")
    return Measured(sum(clean) / len(clean))


def _total(metric: str, steps: Gated) -> Measured:
    gaps = [name for name, values in steps if values[metric].status is not _OK]
    if gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {', '.join(gaps)}")
    return Measured(float(sum(values[metric].value or 0.0 for _, values in steps)))


def effort_totals(steps: Gated) -> dict[str, Measured]:
    """Rates over decided steps and totals over the gated steps."""
    measured = [values for _, values in steps]
    values: dict[str, Measured] = {"gated_steps": Measured(float(len(steps)))}
    values |= {rate: _rate(metric, measured) for rate, metric in RATES.items()}
    values["mean_rounds_to_clean"] = _mean_rounds(measured)
    return values | {total: _total(metric, steps) for total, metric in TOTALS.items()}


# ---- the tests check and the fix rounds' correctness -----------------------------------


def _mean(
    metric: str, steps: Sequence[Mapping[str, Measured]], what: str, *, strict: bool = False
) -> Measured:
    """Mean over the steps that measured ``metric`` (``strict``: unavailable if one lacks it)."""
    gaps = [v[metric] for v in steps if v[metric].status in (_UNAVAILABLE, _INSUFFICIENT)]
    if strict and gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {len(gaps)} step(s)")
    values = [v[metric].value for v in steps if v[metric].status is _OK]
    if not values:
        return Measured.gap(_NA, f"no step measured {what}")
    total = float(sum(value for value in values if value is not None))
    return Measured(total / len(values), numerator=total, denominator=float(len(values)))


def tests_totals(steps: Sequence[Mapping[str, Measured]]) -> dict[str, Measured]:
    return {
        "first_run_tests_pass_rate": _rate("first_run_tests_passed", steps),
        "tests_missing_rate": _rate("first_run_tests_missing", steps),
        "tests_pass_at_step_end_rate": _rate("tests_passed_at_step_end", steps),
        "mean_first_run_coverage": _mean("first_run_coverage", steps, "first-run coverage"),
        "mean_coverage_at_step_end": _mean("coverage_at_step_end", steps, "step-end coverage"),
        "fix_round_correctness_delta": _mean(
            "correctness_delta", steps, "a fix round's delta", strict=True
        ),
    }


# ---- blocking counts that compare across gate modes --------------------------------------


def _share(metric: str, steps: Sequence[Mapping[str, Measured]], what: str) -> Measured:
    """Share of the steps that measured ``metric`` with value 1 (not applicable: none did)."""
    decided = [values[metric].value or 0.0 for values in steps if values[metric].status is _OK]
    if not decided:
        return Measured.gap(_NA, f"no gated step measured {what}")
    numerator = float(sum(decided))
    return Measured(numerator / len(decided), numerator=numerator, denominator=float(len(decided)))


def summed(metric: str, steps: Gated) -> Measured:
    """Sum over the steps; unavailable when a step could not measure it."""
    gaps = [
        name for name, values in steps if values[metric].status in (_UNAVAILABLE, _INSUFFICIENT)
    ]
    if gaps:
        return Measured.gap(_UNAVAILABLE, f"{metric} is unmeasured at {', '.join(gaps)}")
    measured = [values[metric].value or 0.0 for _, values in steps if values[metric].status is _OK]
    if not measured:
        return Measured.gap(_NA, f"no gated step measured {metric}")
    return Measured(float(sum(measured)))


def _final(metric: str, steps: Gated) -> Measured:
    if not steps:
        return Measured.gap(_INSUFFICIENT, "no gated step")
    name, values = steps[-1]
    measured = values[metric]
    if measured.status is _OK:
        return measured
    return Measured.gap(measured.status, f"at {name}: {measured.reason}")


def blocking_totals(steps: Gated) -> dict[str, Measured]:
    measured = [values for _, values in steps]
    return {
        f"total_{NEW_FIRST_RUN_BLOCKING}": summed(NEW_FIRST_RUN_BLOCKING, steps),
        f"final_{STEP_END_BLOCKING}": _final(STEP_END_BLOCKING, steps),
        f"total_{FIRST_RUN_UNTYPED_CALL}": summed(FIRST_RUN_UNTYPED_CALL, steps),
        f"final_{STEP_END_UNTYPED_CALL}": _final(STEP_END_UNTYPED_CALL, steps),
        f"{FIRST_RUN_TESTS_FAILED}_rate": _share(
            FIRST_RUN_TESTS_FAILED, measured, "a first-run test result"
        ),
        f"{FIRST_RUN_COVERAGE_MET}_rate": _share(
            FIRST_RUN_COVERAGE_MET, measured, "first-run coverage"
        ),
        f"total_{FIRST_RUN_FAILED_TESTS}": summed(FIRST_RUN_FAILED_TESTS, steps),
    }


def cross_mode_totals(steps: Gated) -> dict[str, Measured]:
    """The first-pass variant rates and the cascade-free totals."""
    measured = [values for _, values in steps]
    return {f"{name}_rate": _rate(name, measured) for name in FIRST_PASS_VARIANT_WHAT} | {
        TOTAL_NEW_EX_CASCADE: summed(NEW_EX_CASCADE, steps),
        FINAL_STEP_END_EX_CASCADE: _final(STEP_END_EX_CASCADE, steps),
    }


def net_totals(steps: Gated) -> dict[str, Measured]:
    return {
        TOTAL_NEW_NET: summed(NEW_NET, steps),
        FINAL_STEP_END_NET: _final(STEP_END_NET, steps),
    }


# ---- the autofix step ----------------------------------------------------------------------


def autofix_totals(steps: Gated) -> dict[str, Measured]:
    measured = [values for _, values in steps]
    totals = {f"total_{name}": summed(name, steps) for name in AUTOFIX_SUMMED}
    return totals | {
        f"{FIRST_PASS_POST}_rate": _share(FIRST_PASS_POST, measured, "a check after autofix"),
        f"final_{STEP_END_POST}": _final(STEP_END_POST, steps),
    }


def all_totals(steps: Gated) -> dict[str, Measured]:
    """Every trajectory value over the gated steps (``steps``: each step's name and values)."""
    measured = [values for _, values in steps]
    return (
        effort_totals(steps)
        | tests_totals(measured)
        | blocking_totals(steps)
        | cross_mode_totals(steps)
        | net_totals(steps)
        | autofix_totals(steps)
        | {f"total_{name}": summed(name, steps) for name in NEW_METRICS}
    )
