"""Checkpoints and execution validity projected as the surface's verification gates.

There is one ``gate:<checkpoint>`` per checkpoint the trial attempted, passed
exactly when the oracle's strict all-cases verdict solved it, plus the
validity gates: ``gate:infra`` (every execution-validity check except the
component probes) and ``gate:component-probes``. A checkpoint without a
verdict because infrastructure broke keeps its failed gate span, and the
finding explaining it comes from the infra gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.execution.models import (
    CheckpointAttempt,
    CheckpointState,
    TrajectoryRecord,
    ValidityCheck,
)
from raidar.oracle.models import CaseStatus
from raidar.oracle.records import StepRecord, load_step_record
from raidar.review_projection.notes import EvidenceRef, Finding
from raidar.review_projection.spans import SpanBuilder

PROBE_CHECK = "component_probes"
INFRA_GATE = "infra"
PROBE_GATE = "component-probes"
_FAILING_CASE_LIMIT = 50


@dataclass(frozen=True)
class GateContext:
    builder: SpanBuilder
    root: str
    start: int
    trial_dir: Path
    record: TrajectoryRecord


def oracle_record(trial_dir: Path, attempt: CheckpointAttempt) -> StepRecord | None:
    if attempt.oracle_record is None:
        return None
    return load_step_record((trial_dir / attempt.oracle_record).parent)


def attempted(record: TrajectoryRecord) -> list[CheckpointAttempt]:
    return [a for a in record.attempts if a.checkpoint_state is not CheckpointState.NOT_ATTEMPTED]


def _case_summary(step: StepRecord | None) -> dict[str, object]:
    if step is None:
        return {}
    failing = [
        case.id for case in step.grade.cases if case.status in (CaseStatus.FAILED, CaseStatus.ERROR)
    ]
    return {
        "summary": step.grade.summary(),
        "failing_cases": failing[:_FAILING_CASE_LIMIT],
        "failing_case_count": len(failing),
        "infra_failure": (
            step.grade.infra_failure.model_dump() if step.grade.infra_failure else None
        ),
    }


def _case_detail(step: StepRecord | None, attempt: CheckpointAttempt) -> str:
    if attempt.checkpoint_state is CheckpointState.INFRA_ERROR:
        return "no verdict: " + ("; ".join(attempt.infra_reasons) or "infrastructure error")
    if step is None:
        return "no oracle record"
    counts = step.grade.group_counts().values()
    passed, total = sum(c.passed for c in counts), sum(c.total for c in counts)
    return f"{passed}/{total} graded cases passed"


def _checkpoint_gate(ctx: GateContext, parent: str, attempt: CheckpointAttempt) -> Finding | None:
    step = oracle_record(ctx.trial_dir, attempt)
    solved = attempt.checkpoint_state is CheckpointState.SOLVED
    name = f"gate:{attempt.checkpoint}"
    ctx.builder.add(
        name=name,
        span_type="TOOL_CALL",
        status="OK" if solved else "ERROR",
        parent=parent,
        start=ctx.start,
        input_value=f"oracle strict all-cases verdict for {attempt.step}",
        output_value={
            "checkpoint_state": attempt.checkpoint_state.value,
            "strict_pass_rate": attempt.strict_pass_rate,
            "rewards": attempt.rewards,
            "infra_reasons": list(attempt.infra_reasons),
            **_case_summary(step),
        },
    )
    if solved or attempt.checkpoint_state is CheckpointState.INFRA_ERROR:
        return None  # an infrastructure error is reported once, by the infra gate
    detail = _case_detail(step, attempt)
    return Finding(
        id=f"gate-{attempt.checkpoint}",
        kind="issue",
        category="failed-gate",
        title=f"{attempt.checkpoint} not solved",
        detail=detail,
        span=name,
        evidence=(EvidenceRef("oracle", attempt.checkpoint, detail),),
    )


def _validity_finding(gate: str, failed: Sequence[ValidityCheck]) -> Finding:
    return Finding(
        id=f"gate-{gate}",
        kind="issue",
        category="failed-gate",
        title=f"{gate} validity gate failed",
        detail="; ".join(f"{check.name}: {check.detail or 'failed'}" for check in failed),
        span=f"gate:{gate}",
        evidence=tuple(EvidenceRef("validity", gate, check.detail) for check in failed),
    )


def _validity_gate(ctx: GateContext, parent: str, gate: str, probes: bool) -> Finding | None:
    checks = [c for c in ctx.record.validity.checks if (c.name == PROBE_CHECK) is probes]
    if not checks:
        return None
    failed = [check for check in checks if not check.passed]
    name = f"gate:{gate}"
    ctx.builder.add(
        name=name,
        span_type="TOOL_CALL",
        status="ERROR" if failed else "OK",
        parent=parent,
        start=ctx.start,
        input_value=f"execution validity: {', '.join(check.name for check in checks)}",
        output_value={
            "checks": [check.model_dump(mode="json") for check in checks],
            "trial_exception": ctx.record.trial_exception,
        },
    )
    return _validity_finding(gate, failed) if failed else None


def _clean(record: TrajectoryRecord) -> Finding | None:
    states = {attempt.checkpoint_state for attempt in record.attempts}
    if states != {CheckpointState.SOLVED} or not record.valid:
        return None
    return Finding(
        id="clean-verification",
        kind="good",
        category="clean-verification",
        title="Every checkpoint solved",
        detail=f"all {len(record.attempts)} checkpoints passed the oracle's strict verdict",
        span="verification gates",
    )


def project_gates(ctx: GateContext) -> list[Finding]:
    """Add the gate spans and return the findings they support."""
    attempts = attempted(ctx.record)
    failing = any(a.checkpoint_state is not CheckpointState.SOLVED for a in attempts)
    parent = ctx.builder.add(
        name="verification gates",
        span_type="INTERNAL",
        status="ERROR" if failing or not ctx.record.valid else "OK",
        parent=ctx.root,
        start=ctx.start,
    )
    findings: Sequence[Finding | None] = [
        *(_checkpoint_gate(ctx, parent, attempt) for attempt in attempts),
        _validity_gate(ctx, parent, INFRA_GATE, probes=False),
        _validity_gate(ctx, parent, PROBE_GATE, probes=True),
        _clean(ctx.record),
    ]
    return [finding for finding in findings if finding is not None]


def failed_gates(builder: SpanBuilder) -> list[str]:
    return [
        span.name.removeprefix("gate:")
        for span in builder.spans
        if span.name.startswith("gate:") and span.status == "ERROR"
    ]
