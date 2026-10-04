"""``gate-effort@2``: gate-effort@1, plus a shadow gate's first-run findings.

``gate-effort@1`` (``raidar.scorers.gate_effort``) is released and unchanged. @2
has the same metrics and reads the same ``guardrail-gate`` records (from
guardrail-gate@1 or @2), and adds one rule for a step whose record says
``mode: shadow`` (guardrail-gate@2: the commit check ran once at the step's
end and was never enforced):

- measured, as for an enforced gate: ``gate_runs``, ``first_pass_clean``,
  ``gate_error`` and ``first_run_blocking`` (in total and per rule family), so
  a baseline arm with a shadow gate and a treatment arm with the real gate have
  paired first-run outcomes (``first_pass_clean_rate``, ``gate_error_rate``,
  ``total_first_run_blocking``);
- ``not_applicable`` with the reason ``SHADOW_REASON``: every fix-round, fix-
  session and enforcement outcome (``fix_rounds``, ``gate_clean_at_step_end``,
  ``unresolved``, ``rounds_to_clean``, ``blocking_findings`` over every run,
  ``fix_*``, ``gate_wall_s``) and their trajectory rates and totals. A shadow
  gate starts no fix session, and its run time is Raidar's overhead, not the
  arm's work; none of them is ever a zero.

A trajectory whose gated steps mix shadow and enforced records is an
``error`` (one condition runs one gate mode). Every other rule, status and
reason is gate-effort@1's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from raidar.scorers.gate_effort.families import FAMILIES
from raidar.scorers.gate_effort.manifest import BLOCKING_FINDINGS, GATE_EFFORT
from raidar.scorers.gate_effort.records import RECORD_PATH
from raidar.scorers.gate_effort.scorer import (
    TOTALS,
    Measured,
    _checkpoint,
    _evidence,
    _read,
    _Step,
    trajectory_values,
)
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

GATE_EFFORT_V2 = ScorerManifest(
    id="gate-effort",
    version="2",
    description=(
        "Effort a guardrail-gate commit gate cost per step: rounds to clean, first-pass "
        "clean, unresolved and gate-error steps, blocking findings by rule family, and the "
        "fix sessions' tokens, cost and time. A shadow gate (guardrail-gate@2 mode shadow) "
        "reports its first-run outcomes only; its fix and enforcement outcomes are not "
        "applicable. Not applicable to an arm without a gate."
    ),
    runtimes=GATE_EFFORT.runtimes,
    evidence_required=GATE_EFFORT.evidence_required,
    metrics=GATE_EFFORT.metrics,
)

NO_GATE = "no guardrail-gate record: this trajectory ran without a commit gate"
SHADOW_MODE = "shadow"
SHADOW_REASON = (
    "shadow gate: the commit check was recorded at the step's end and never enforced "
    "(no fix session, no refusal)"
)
_NA = AssessStatus.NOT_APPLICABLE
_OK = AssessStatus.OK
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


def _shadowed(values: Mapping[str, Measured], names: tuple[str, ...]) -> dict[str, Measured]:
    """``values`` with ``names`` not applicable (a shadow gate enforced nothing)."""
    gap = Measured.gap(_NA, SHADOW_REASON)
    return {
        metric: gap if metric in names and measured.status is not _NA else measured
        for metric, measured in values.items()
    }


def _modes(steps: list[_Step]) -> set[bool]:
    """For every step with a gate record: whether it was a shadow run."""
    return {step_mode(step.inputs.trace_dir) == SHADOW_MODE for step in steps if step.gate}


def _observations(steps: list[_Step], shadow: bool) -> list[Observation]:
    """Every checkpoint's and the trajectory's observations; a shadow gate's fix side is n/a."""
    per_step = [(step, _checkpoint(step)) for step in steps]
    if shadow:
        per_step = [(step, _shadowed(values, SHADOW_STEP_NA)) for step, values in per_step]
    totals = trajectory_values(
        [(step.inputs.step, values) for step, values in per_step if step.gated]
    )
    if shadow:
        totals = _shadowed(totals, SHADOW_TOTAL_NA)
    return [
        measured.observation(metric, step.inputs.step)
        for step, values in per_step
        for metric, measured in values.items()
    ] + [measured.observation(metric, TRAJECTORY_SUBJECT) for metric, measured in totals.items()]


class GateEffortV2Scorer:
    """Deterministic scorer over each step's guardrail-gate record, shadow records included."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V2

    def assess(self, ctx: AssessContext) -> AssessResult:
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V2, ctx, AssessStatus.ERROR, "gate-effort@2 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V2, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V2, ctx, AssessStatus.ERROR, reason)
        observations = _observations(steps, shadow=modes == {True})
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else AssessStatus.INSUFFICIENT_EVIDENCE,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=_evidence(steps),
        )


def gate_effort_v2_scorer() -> GateEffortV2Scorer:
    """Registry factory for ``gate-effort@2``."""
    return GateEffortV2Scorer()


__all__ = [
    "GATE_EFFORT_V2",
    "SHADOW_REASON",
    "GateEffortV2Scorer",
    "gate_effort_v2_scorer",
    "step_mode",
]
