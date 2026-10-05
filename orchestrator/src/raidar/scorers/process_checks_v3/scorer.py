"""``process-compliance@3``: process-compliance@2 and whether the agent stalled on a question.

``process-compliance@2`` (``raidar.scorers.process_checks``) is released and
unchanged; @3 keeps every @2 metric, value, status and reason, and adds the
stall outcomes read deterministically from the step's
``continue-on-stall.json`` records (``continue-on-stall@1`` writes one per
session, stalled or not, beside that session's logs):

- ``stalled`` (checkpoint): a session of the step changed no workspace file
  and ended asking a question or for clarification (the wrapper's
  deterministic detection);
- ``stall_resolved`` (checkpoint): every continuation the step's stalls
  started changed the workspace; ``not_applicable`` on a step that did not
  stall;
- ``stall_rate`` (trajectory): stalled steps over the reached steps with a
  record (numerator and denominator reported); ``stalled_steps``: their count.

A trajectory with no record in any reached step ran without the wrapper:
every stall observation is ``not_applicable``, never 0. In a trajectory that
has records, a reached step without one is ``unavailable``, and an unreached
step ``not_applicable``. The record contract is repeated here, not imported
from the component, so the scorer's identity does not move with the wrapper.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks.scorer import DeterministicProcessComplianceScorer
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

STALL_RECORD = "continue-on-stall.json"
STALLED, RESOLVED = "stalled", "stall_resolved"
STALL_RATE, STALLED_STEPS = "stall_rate", "stalled_steps"
NO_WRAPPER = "no continue-on-stall record: this trajectory ran without the stall wrapper"
_OK = AssessStatus.OK
_NA = AssessStatus.NOT_APPLICABLE


def _metric(
    metric_id: str, description: str, level: MetricLevel, kind: MetricType, direction: Direction
) -> MetricSpec:
    return MetricSpec(
        id=metric_id,
        description=description,
        level=level,
        type=kind,
        direction=direction,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    )


STALL_METRICS = (
    _metric(
        STALLED,
        "A session of the step changed no workspace file and ended asking a question "
        "(continue-on-stall@1 detection).",
        MetricLevel.CHECKPOINT,
        MetricType.BOOLEAN,
        Direction.LOWER_BETTER,
    ),
    _metric(
        RESOLVED,
        "Every continuation a stall started changed the workspace.",
        MetricLevel.CHECKPOINT,
        MetricType.BOOLEAN,
        Direction.HIGHER_BETTER,
    ),
    _metric(
        STALL_RATE,
        "Share of reached steps with a stall record in which a session stalled.",
        MetricLevel.TRAJECTORY,
        MetricType.RATIO,
        Direction.LOWER_BETTER,
    ),
    _metric(
        STALLED_STEPS,
        "Reached steps in which a session stalled.",
        MetricLevel.TRAJECTORY,
        MetricType.COUNT,
        Direction.LOWER_BETTER,
    ),
)

PROCESS_COMPLIANCE_V3 = ScorerManifest(
    id="process-compliance",
    version="3",
    description=(
        "Deterministic trace checks of agent process; a check the trace cannot decide is "
        "undetermined, never judged by a model. Adds whether a session stalled on a "
        "question (continue-on-stall@1 records) and the stall rate."
    ),
    runtimes=PROCESS_COMPLIANCE_V2.runtimes,
    evidence_required=PROCESS_COMPLIANCE_V2.evidence_required,
    metrics=(*PROCESS_COMPLIANCE_V2.metrics, *STALL_METRICS),
)


def _records(trace_dir: Path | None) -> list[dict[str, Any]] | None:
    """The step's stall records (None: none found)."""
    if trace_dir is None or not trace_dir.is_dir():
        return None
    found = []
    for path in sorted(trace_dir.rglob(STALL_RECORD)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("stalled"), bool):
            found.append(payload)
    return found or None


def _gap(metric: str, subject: str, status: AssessStatus, reason: str) -> Observation:
    return Observation(metric=metric, subject=subject, status=status, reason=reason)


def _flag(metric: str, subject: str, value: bool) -> Observation:
    return Observation(metric=metric, subject=subject, status=_OK, value=1.0 if value else 0.0)


def _step(inputs: StepInputs, records: list[dict[str, Any]] | None) -> tuple[Observation, ...]:
    subject = inputs.step
    if not inputs.reached:
        return tuple(_gap(m, subject, _NA, "checkpoint not reached") for m in (STALLED, RESOLVED))
    if records is None:
        reason = "no continue-on-stall record for this step, though other steps have one"
        return tuple(
            _gap(m, subject, AssessStatus.UNAVAILABLE, reason) for m in (STALLED, RESOLVED)
        )
    stalls = [record for record in records if record["stalled"]]
    if not stalls:
        return (
            _flag(STALLED, subject, False),
            _gap(RESOLVED, subject, _NA, "the step did not stall"),
        )
    changed = [(record.get("continuation") or {}).get("changed") is True for record in stalls]
    return (_flag(STALLED, subject, True), _flag(RESOLVED, subject, all(changed)))


def _totals(per_step: Sequence[tuple[Observation, ...]]) -> tuple[Observation, ...]:
    decided = [obs[0] for obs in per_step if obs[0].status is _OK]
    gaps = [obs[0].subject for obs in per_step if obs[0].status is AssessStatus.UNAVAILABLE]
    if gaps:
        reason = f"stalled is unavailable at {', '.join(gaps)}"
        return tuple(
            _gap(m, TRAJECTORY_SUBJECT, AssessStatus.UNAVAILABLE, reason)
            for m in (STALL_RATE, STALLED_STEPS)
        )
    stalled = float(sum(item.value or 0.0 for item in decided))
    return (
        Observation(
            metric=STALL_RATE,
            subject=TRAJECTORY_SUBJECT,
            status=_OK,
            value=stalled / len(decided),
            numerator=stalled,
            denominator=float(len(decided)),
        ),
        Observation(metric=STALLED_STEPS, subject=TRAJECTORY_SUBJECT, status=_OK, value=stalled),
    )


def stall_observations(ctx: AssessContext) -> tuple[Observation, ...]:
    """Every stall observation of the trajectory (all not applicable without the wrapper)."""
    records = [
        (inputs, _records(inputs.trace_dir) if inputs.reached else None) for inputs in ctx.steps
    ]
    if not any(found for _, found in records):
        return tuple(
            _gap(metric.id, subject, _NA, NO_WRAPPER)
            for metric in STALL_METRICS
            for subject in ctx.subjects(metric.level)
        )
    per_step = [_step(inputs, found) for inputs, found in records]
    return (*(item for step in per_step for item in step), *_totals(per_step))


class ProcessComplianceV3Scorer(DeterministicProcessComplianceScorer):
    """process-compliance@2's checks plus the stall outcomes."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE_V3

    def assess(self, ctx: AssessContext) -> AssessResult:
        base = super().assess(ctx)
        if base.status is AssessStatus.ERROR:
            return base.model_copy(
                update={
                    "observations": (
                        *base.observations,
                        *(
                            _gap(m.id, s, AssessStatus.ERROR, base.reason or "error")
                            for m in STALL_METRICS
                            for s in ctx.subjects(m.level)
                        ),
                    )
                }
            )
        observations = (*base.observations, *stall_observations(ctx))
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else base.status,
            reason=None if measured else base.reason,
            observations=observations,
            evidence=base.evidence,
            provenance=base.provenance,
        )


def process_compliance_v3_scorer() -> ProcessComplianceV3Scorer:
    """Registry factory for ``process-compliance@3``."""
    return ProcessComplianceV3Scorer()
