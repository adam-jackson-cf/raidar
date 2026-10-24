"""A shadow gate's values: its fix and enforcement side is not applicable.

A shadow gate (``guardrail-gate`` mode ``shadow``) records its commit check at the step's end
and never enforces it (no fix session, no refusal). A trajectory whose gated steps mix shadow
and enforced records is an ``error`` (one condition runs one gate mode).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort.measured import Measured
from raidar.scorers.gate_effort.metrics import BLOCKING_FINDINGS
from raidar.scorers.gate_effort.outcomes import Step
from raidar.scorers.gate_effort.records import RECORD_PATH
from raidar.scorers.gate_effort.totals import TOTALS
from raidar.scorers.sdk.result import AssessStatus

SHADOW_MODE = "shadow"
SHADOW_REASON = (
    "shadow gate: the commit check was recorded at the step's end and never enforced "
    "(no fix session, no refusal)"
)
_NA = AssessStatus.NOT_APPLICABLE
SHADOW_STEP_NA = (
    "fix_rounds",
    "gate_clean_at_step_end",
    "unresolved",
    "rounds_to_clean",
    BLOCKING_FINDINGS,
    *(f"{BLOCKING_FINDINGS}.{family}" for family in FAMILIES),
    "fix_input_tokens",
    "fix_output_tokens",
    "fix_cost_usd",
    "fix_wall_s",
    "gate_wall_s",
)
SHADOW_TOTAL_NA = (
    "clean_at_step_end_rate",
    "unresolved_rate",
    "mean_rounds_to_clean",
    *(total for total, metric in TOTALS.items() if metric in SHADOW_STEP_NA),
)


def step_mode(trace_dir: Path | None) -> str | None:
    """The gate mode a step's record names (``None``: no record, or a record without one)."""
    if trace_dir is None:
        return None
    try:
        payload = json.loads((trace_dir / RECORD_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    mode = payload.get("mode") if isinstance(payload, dict) else None
    return mode if isinstance(mode, str) else None


def shadowed(values: Mapping[str, Measured], names: tuple[str, ...]) -> dict[str, Measured]:
    """``values`` with ``names`` not applicable (a shadow gate enforced nothing)."""
    gap = Measured.gap(_NA, SHADOW_REASON)
    return {
        metric: gap if metric in names and measured.status is not _NA else measured
        for metric, measured in values.items()
    }


def gate_modes(steps: list[Step]) -> set[bool]:
    """For every step with a gate record: whether it was a shadow run."""
    return {step_mode(step.inputs.trace_dir) == SHADOW_MODE for step in steps if step.gate}
