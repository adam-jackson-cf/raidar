"""The ``new_*`` counts: a step's first-run count minus what the previous gated step handed on."""

from __future__ import annotations

from raidar.scorers.gate_effort.measured import Measured
from raidar.scorers.gate_effort.outcomes import Step
from raidar.scorers.sdk.result import AssessStatus

_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE
_UNAVAILABLE = AssessStatus.UNAVAILABLE

PerStep = list[tuple[Step, dict[str, Measured]]]


def with_new(per_step: PerStep, first_name: str, end_name: str, new_name: str) -> None:
    """``new_name`` = ``first_name`` minus the previous gated step's ``end_name`` (0 before any).

    Signed: a step that removes debt is negative. A reached step without a gate record changed
    the tree unseen, so the next step's baseline is unknown.
    """
    previous = Measured(0.0)
    for step, values in per_step:
        if not step.inputs.reached:
            values[new_name] = Measured.gap(_NA, "checkpoint not reached")
            continue
        if not step.gated:
            reason = f"{step.inputs.step} has no gate record"
            values[new_name] = Measured.gap(_UNAVAILABLE, reason)
            previous = values[new_name]
            continue
        first = values[first_name]
        if first.status is not _OK:
            values[new_name] = first
        elif previous.status is not _OK or previous.value is None:
            reason = f"the previous gated step's end count is unknown: {previous.reason}"
            values[new_name] = Measured.gap(_UNAVAILABLE, reason)
        else:
            values[new_name] = Measured((first.value or 0.0) - previous.value)
        previous = values[end_name]
