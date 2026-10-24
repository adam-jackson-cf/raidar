"""The deterministic core of ``process-compliance``.

One step's ATIF trajectory (every session, including review-loop and
sub-agent sessions) is classified into process events (``trace_events``) and
the checks run over them (``checks``). This module turns check outcomes into
observations and evidence and combines steps into one result; it never asks a
model.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain

from pydantic import Field, field_validator

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.manifest import SdkModel
from raidar.scoring.framework.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Provenance,
    Span,
)
from raidar.scoring.process_checks.checks import CheckOutcome, Verdict
from raidar.scoring.process_checks.trace_events import (
    DEFAULT_PLAN_FILE_PATTERNS,
    DEFAULT_TEST_PATTERNS,
    DEFAULT_WORKDIR,
    Classifier,
)
from raidar.scoring.resource_usage.atif import Trajectory, TrajectoryError, load_trajectory

RATE_METRIC = "compliance_rate"
UNDETERMINED = "undetermined"


class ProcessComplianceConfig(SdkModel):
    """Assessor-owned settings; extra patterns extend the defaults.

    ``plan_in_compliance_rate``: the condition asks for a plan, so ``plan_written``
    counts in ``compliance_rate`` (otherwise only ``tests_run_before_finish`` does).
    """

    workdir: str = Field(default=DEFAULT_WORKDIR, pattern=r"^/")
    extra_test_patterns: tuple[str, ...] = ()
    extra_plan_file_patterns: tuple[str, ...] = ()
    plan_in_compliance_rate: bool = False

    @field_validator("extra_test_patterns", "extra_plan_file_patterns")
    @classmethod
    def _compiles(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"pattern {pattern!r} does not compile: {exc}") from exc
        return patterns

    def classifier(self) -> Classifier:
        return Classifier(
            workdir=self.workdir,
            test_patterns=DEFAULT_TEST_PATTERNS + self.extra_test_patterns,
            plan_file_patterns=DEFAULT_PLAN_FILE_PATTERNS + self.extra_plan_file_patterns,
        )


@dataclass(frozen=True)
class StepAssessment:
    """One step's observations, evidence and decided-check tally."""

    observations: tuple[Observation, ...]
    evidence: tuple[Evidence, ...] = ()
    passed: int = 0
    decided: int = 0
    unreadable: bool = False


def statused(
    step: str, status: AssessStatus, reason: str, metrics: Sequence[str]
) -> StepAssessment:
    return StepAssessment(
        tuple(
            Observation(metric=metric, subject=step, status=status, reason=reason)
            for metric in metrics
        ),
        unreadable=status is AssessStatus.UNAVAILABLE,
    )


def check_observation(step: str, outcome: CheckOutcome, unsettled: str) -> Observation:
    """A decided check is ``ok`` (1 or 0); an unsettled one never carries a value.

    ``unsettled`` labels the reason of a check no rule decides; its status is
    ``insufficient_evidence``, so it is never counted as a failure.
    """
    if outcome.verdict in (Verdict.PASS, Verdict.FAIL):
        value = 1.0 if outcome.verdict is Verdict.PASS else 0.0
        return Observation(metric=outcome.check, subject=step, status=AssessStatus.OK, value=value)
    if outcome.verdict is Verdict.NOT_APPLICABLE:
        return Observation(
            metric=outcome.check,
            subject=step,
            status=AssessStatus.NOT_APPLICABLE,
            reason=outcome.reason,
        )
    return Observation(
        metric=outcome.check,
        subject=step,
        status=AssessStatus.INSUFFICIENT_EVIDENCE,
        reason=f"{unsettled}: {outcome.reason}",
    )


def _call_line(lines: Sequence[str], call_id: str) -> int | None:
    needle = f'"{call_id}"'
    return next(
        (
            number
            for number, line in enumerate(lines, start=1)
            if needle in line and "tool_call_id" in line
        ),
        None,
    )


def _trace_evidence(
    step: str, outcome: CheckOutcome, trajectory: Trajectory, sha256: str, lines: Sequence[str]
) -> Iterable[Evidence]:
    for event in outcome.events:
        line = _call_line(lines, event.call_id) if event.call_id else None
        if line is not None:
            yield Evidence(
                metric=outcome.check,
                subject=step,
                source=EvidenceSource.TRACE,
                path=trajectory.path.name,
                sha256=sha256,
                kind=f"trace-{event.kind.value}",
                spans=(Span(line=line),),
                message=f"{outcome.verdict.value}: {event.detail}",
            )


def _snapshot_evidence(inputs: StepInputs, outcome: CheckOutcome) -> Iterable[Evidence]:
    for event in outcome.events:
        path = inputs.snapshot / event.path if inputs.snapshot and event.path else None
        if path is None or not path.is_file():
            continue
        data = path.read_bytes()
        if not data:
            continue
        yield Evidence(
            metric=outcome.check,
            subject=inputs.step,
            path=event.path or "",
            sha256=hashlib.sha256(data).hexdigest(),
            kind="plan-file",
            spans=(Span(line=1),),
            message=f"plan file present at the checkpoint: {event.path}",
        )


def step_evidence(
    inputs: StepInputs, outcomes: Sequence[CheckOutcome], trajectory: Trajectory
) -> tuple[Evidence, ...]:
    """Evidence at the deciding tool call (and a passing plan file) of each decided check."""
    data = trajectory.path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8", errors="replace").split("\n")
    evidence: list[Evidence] = []
    for outcome in outcomes:
        if outcome.verdict not in (Verdict.PASS, Verdict.FAIL):
            continue
        evidence += _trace_evidence(inputs.step, outcome, trajectory, sha256, lines)
        if outcome.verdict is Verdict.PASS:
            evidence += _snapshot_evidence(inputs, outcome)
    return tuple(evidence)


def measurable(
    ctx: AssessContext, inputs: StepInputs, metrics: Sequence[str]
) -> Trajectory | StepAssessment:
    """The step's trajectory, or its statused assessment when it cannot be measured."""
    if not inputs.reached:
        return statused(inputs.step, AssessStatus.NOT_APPLICABLE, "checkpoint not reached", metrics)
    inventory = ctx.inventory(inputs.step)
    if inventory is not None and not inventory.eligible:
        detail = "; ".join(issue.detail for issue in inventory.invalid)
        return statused(
            inputs.step, AssessStatus.INELIGIBLE, f"invalid snapshot: {detail}", metrics
        )
    try:
        trajectory = load_trajectory(inputs.trace_dir)
    except TrajectoryError as exc:
        return statused(
            inputs.step, AssessStatus.UNAVAILABLE, f"trajectory unreadable: {exc}", metrics
        )
    if trajectory is None:
        return statused(
            inputs.step, AssessStatus.UNAVAILABLE, "the step has no ATIF trajectory", metrics
        )
    return trajectory


def combine(
    steps: Sequence[tuple[StepInputs, StepAssessment]], provenance: Provenance
) -> AssessResult:
    assessed = [step for _, step in steps]
    observations = [*chain.from_iterable(step.observations for step in assessed), _rate(steps)]
    measured = any(item.status is AssessStatus.OK for item in observations)
    return AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
        reason=None if measured else "no step has a decidable process check",
        observations=tuple(observations),
        evidence=tuple(chain.from_iterable(step.evidence for step in assessed)),
        provenance=provenance,
    )


def _rate(steps: Sequence[tuple[StepInputs, StepAssessment]]) -> Observation:
    unreadable = [inputs.step for inputs, step in steps if step.unreadable]
    if unreadable:
        return Observation(
            metric=RATE_METRIC,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=f"no readable trajectory for {unreadable}",
        )
    passed = sum(step.passed for _, step in steps)
    decided = sum(step.decided for _, step in steps)
    if decided == 0:
        return Observation(
            metric=RATE_METRIC,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="no deterministically decided check in the trajectory",
        )
    return Observation(
        metric=RATE_METRIC,
        subject=TRAJECTORY_SUBJECT,
        status=AssessStatus.OK,
        value=passed / decided,
        numerator=passed,
        denominator=decided,
    )
