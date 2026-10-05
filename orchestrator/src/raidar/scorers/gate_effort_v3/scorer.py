"""``gate-effort@3``: gate-effort@2, but a step with no source change is not first-pass clean.

``gate-effort@2`` (``raidar.scorers.gate_effort_v2``) is released and unchanged.
The commit gate commits with ``--allow-empty``, so a step in which the agent
changed no source file (it stopped to ask a question, say) passes the gate's
first run vacuously: nothing new was checked. @2 counts that step as
``first_pass_clean = 1``; @3 reports it ``not_applicable`` with the reason
``NO_SOURCE_CHANGE`` and leaves it out of ``first_pass_clean_rate``.

"No source change" is deterministic: the step's snapshot holds the same
production and test files, byte for byte (the assessor-owned inventory), as the
previous step's snapshot (for the first step: the problem's starter, when the
context has one). A step whose comparison cannot be made (no snapshot, or a
first step without a starter) keeps the @2 value. Every other metric, status
and reason is gate-effort@2's (shadow rule included); ``first_run_blocking``
stays measured, since it counts what the gate found in the step's tree.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.scorer import (
    Measured,
    _checkpoint,
    _evidence,
    _read,
    _Step,
    trajectory_values,
)
from raidar.scorers.gate_effort_v2.scorer import (
    GATE_EFFORT_V2,
    NO_GATE,
    SHADOW_STEP_NA,
    SHADOW_TOTAL_NA,
    _modes,
    _shadowed,
)
from raidar.scorers.inventory import FileRole, Inventory, build_inventory
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import ScorerManifest
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

GATE_EFFORT_V3 = ScorerManifest(
    id="gate-effort",
    version="3",
    description=(
        "Effort a guardrail-gate commit gate cost per step: rounds to clean, first-pass "
        "clean, unresolved and gate-error steps, blocking findings by rule family, and the "
        "fix sessions' tokens, cost and time. A shadow gate (guardrail-gate@2 mode shadow) "
        "reports its first-run outcomes only. A step that changed no source file is not "
        "applicable to first-pass clean. Not applicable to an arm without a gate."
    ),
    runtimes=GATE_EFFORT_V2.runtimes,
    evidence_required=GATE_EFFORT_V2.evidence_required,
    metrics=GATE_EFFORT_V2.metrics,
)

FIRST_PASS_CLEAN = "first_pass_clean"
NO_SOURCE_CHANGE = (
    "no source change in the step: its production and test files equal the previous "
    "snapshot's, so the gate's first run checked nothing new"
)
_SOURCE_ROLES = (FileRole.PRODUCTION, FileRole.TEST)
_NA = AssessStatus.NOT_APPLICABLE
_OK = AssessStatus.OK

SourceMap = dict[str, str | None]


def _sources(inventory: Inventory | None) -> SourceMap | None:
    if inventory is None:
        return None
    return {item.path: item.sha256 for item in inventory.files if item.role in _SOURCE_ROLES}


def _step_sources(ctx: AssessContext, name: str) -> SourceMap | None:
    try:
        return _sources(ctx.inventory(name))
    except (OSError, NotADirectoryError):
        return None


def _baseline_sources(ctx: AssessContext, name: str) -> SourceMap | None:
    """The source files before ``name`` ran: the prior step's, or the starter's."""
    prior = ctx.prior(name)
    if prior is not None:
        return _step_sources(ctx, prior.step) if prior.reached else None
    if ctx.starter is None:
        return None
    try:
        return _sources(build_inventory(ctx.starter, ctx.inventory_policy))
    except (OSError, NotADirectoryError):
        return None


def unchanged_steps(ctx: AssessContext) -> frozenset[str]:
    """Reached steps whose production and test files equal the previous snapshot's."""
    unchanged: set[str] = set()
    for inputs in ctx.steps:
        if not inputs.reached:
            continue
        before = _baseline_sources(ctx, inputs.step)
        after = _step_sources(ctx, inputs.step) if before is not None else None
        if after is not None and after == before:
            unchanged.add(inputs.step)
    return frozenset(unchanged)


def _step_values(step: _Step, shadow: bool, unchanged: frozenset[str]) -> dict[str, Measured]:
    """One checkpoint's @2 values, with a no-edit step's first pass not applicable."""
    values = _checkpoint(step)
    if shadow:
        values = _shadowed(values, SHADOW_STEP_NA)
    no_edit = step.gate is not None and step.inputs.step in unchanged
    if no_edit and values[FIRST_PASS_CLEAN].status is _OK:
        values = values | {FIRST_PASS_CLEAN: Measured.gap(_NA, NO_SOURCE_CHANGE)}
    return values


def _observations(steps: list[_Step], shadow: bool, unchanged: frozenset[str]) -> list[Observation]:
    per_step = [(step, _step_values(step, shadow, unchanged)) for step in steps]
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


class GateEffortV3Scorer:
    """gate-effort@2 over each step's gate record; a no-edit step is not first-pass clean."""

    @property
    def manifest(self) -> ScorerManifest:
        return GATE_EFFORT_V3

    def assess(self, ctx: AssessContext) -> AssessResult:
        if ctx.config:
            return statused_result(
                GATE_EFFORT_V3, ctx, AssessStatus.ERROR, "gate-effort@3 takes no config"
            )
        steps = [_read(inputs) for inputs in ctx.steps]
        if not any(step.gated for step in steps):
            return statused_result(GATE_EFFORT_V3, ctx, _NA, NO_GATE)
        modes = _modes(steps)
        if len(modes) > 1:
            reason = "the trajectory mixes shadow and enforced gate records"
            return statused_result(GATE_EFFORT_V3, ctx, AssessStatus.ERROR, reason)
        observations = _observations(steps, modes == {True}, unchanged_steps(ctx))
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else AssessStatus.INSUFFICIENT_EVIDENCE,
            reason=None if measured else "no gate run decided anything in this trajectory",
            observations=tuple(observations),
            evidence=_evidence(steps),
        )


def gate_effort_v3_scorer() -> GateEffortV3Scorer:
    """Registry factory for ``gate-effort@3``."""
    return GateEffortV3Scorer()


__all__ = [
    "GATE_EFFORT_V3",
    "NO_SOURCE_CHANGE",
    "GateEffortV3Scorer",
    "gate_effort_v3_scorer",
    "unchanged_steps",
]
