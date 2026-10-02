"""``process-compliance@1``: deterministic trace checks plus a Jev shadow (plan G5).

For each reached checkpoint the step's ATIF trajectory (every session,
including review-loop and sub-agent sessions) is classified into process
events (``trace_events``) and two checks run over them (``checks``):

- ``tests_run_before_finish``: tests ran after the last source edit;
- ``plan_written``: a plan tool or plan file came before the first edit.

A decided check is ``ok`` (1 pass, 0 fail) with evidence at the deciding tool
call in ``trajectory.json`` (and, for a plan file still present, the file in
the snapshot). A step with no source edit is ``not_applicable``. An
ambiguous check is ``insufficient_evidence``; with a ``JevRuntime`` bound
(``jev_residue``), the step's ``jev_ambiguous_compliance`` carries the Jev
probability that the agent complied (tier ``jev``; never an input to a
Metric), and every deterministic decision is audited too. The Jev mode
is the runtime's versioned ``process-compliance.residue`` policy, recorded in
the result's provenance under the metric id; with no runtime it is ``off``.
A step whose
snapshot inventory is invalid is ``ineligible``, as the SDK requires for
scorers that read snapshots.

``compliance_rate`` is passed / decided checks over the eligible reached
checkpoints; it is ``unavailable`` when a reached step has no readable
trajectory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from raidar.scorers.builtin_manifests import PROCESS_COMPLIANCE
from raidar.scorers.process_compliance.checks import CheckOutcome, Verdict, run_checks
from raidar.scorers.process_compliance.jev_residue import (
    JEV_METRIC,
    JevRuntime,
    ask,
    audit_settled,
    policy_modes,
    store_runtime,
    unanswered_decisions,
)
from raidar.scorers.process_compliance.trace_events import (
    DEFAULT_PLAN_FILE_PATTERNS,
    DEFAULT_TEST_PATTERNS,
    DEFAULT_WORKDIR,
    Classifier,
    TraceEvent,
    classify,
)
from raidar.scorers.resource_usage.atif import Trajectory, TrajectoryError, load_trajectory
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest, SdkModel
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Evidence,
    EvidenceSource,
    Observation,
    Provenance,
    Span,
)

RATE_METRIC = "compliance_rate"
_CHECKPOINT_METRICS = tuple(
    metric.id for metric in PROCESS_COMPLIANCE.metrics if metric.level is MetricLevel.CHECKPOINT
)


class ProcessComplianceConfig(SdkModel):
    """Assessor-owned settings; extra patterns extend the defaults."""

    workdir: str = Field(default=DEFAULT_WORKDIR, pattern=r"^/")
    extra_test_patterns: tuple[str, ...] = ()
    extra_plan_file_patterns: tuple[str, ...] = ()

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
    jev_model: str | None = None


def _statused(step: str, status: AssessStatus, reason: str) -> StepAssessment:
    return StepAssessment(
        tuple(
            Observation(metric=metric, subject=step, status=status, reason=reason)
            for metric in _CHECKPOINT_METRICS
        ),
        unreadable=status is AssessStatus.UNAVAILABLE,
    )


def _check_observation(step: str, outcome: CheckOutcome) -> Observation:
    if outcome.verdict in (Verdict.PASS, Verdict.FAIL):
        value = 1.0 if outcome.verdict is Verdict.PASS else 0.0
        return Observation(metric=outcome.check, subject=step, status=AssessStatus.OK, value=value)
    status = (
        AssessStatus.NOT_APPLICABLE
        if outcome.verdict is Verdict.NOT_APPLICABLE
        else AssessStatus.INSUFFICIENT_EVIDENCE
    )
    reason = (
        outcome.reason if status is AssessStatus.NOT_APPLICABLE else f"ambiguous: {outcome.reason}"
    )
    return Observation(metric=outcome.check, subject=step, status=status, reason=reason)


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


def _evidence(
    inputs: StepInputs, outcomes: Sequence[CheckOutcome], trajectory: Trajectory
) -> tuple[Evidence, ...]:
    data = trajectory.path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    lines = data.decode("utf-8", errors="replace").splitlines()
    evidence: list[Evidence] = []
    for outcome in outcomes:
        if outcome.verdict not in (Verdict.PASS, Verdict.FAIL):
            continue
        evidence += _trace_evidence(inputs.step, outcome, trajectory, sha256, lines)
        if outcome.verdict is Verdict.PASS:
            evidence += _snapshot_evidence(inputs, outcome)
    return tuple(evidence)


class ProcessComplianceScorer:
    """Deterministic checks, with an optional Jev runtime for audit and the residue."""

    def __init__(self, jev: JevRuntime | None = None) -> None:
        self._jev = jev
        self._awaiting = False

    def awaiting_answers(self) -> bool:
        """True when the last ``assess`` left a Jev decision unanswered (no key, no cache)."""
        return self._awaiting

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE

    def _jev_observation(
        self, step: str, trajectory: Trajectory, ambiguous: Sequence[CheckOutcome]
    ) -> tuple[Observation, str | None]:
        if not ambiguous:
            return Observation(
                metric=JEV_METRIC,
                subject=step,
                status=AssessStatus.NOT_APPLICABLE,
                reason="no ambiguous check in the step",
            ), None
        if self._jev is None:
            return Observation(
                metric=JEV_METRIC,
                subject=step,
                status=AssessStatus.UNAVAILABLE,
                reason="no Jev runtime is bound to the scorer",
            ), None
        answer = ask(self._jev, step, trajectory, ambiguous)
        if answer.value is None:
            observation = Observation(
                metric=JEV_METRIC,
                subject=step,
                status=AssessStatus.UNAVAILABLE,
                reason=answer.reason,
            )
        else:
            observation = Observation(
                metric=JEV_METRIC, subject=step, status=AssessStatus.OK, value=answer.value
            )
        return observation, answer.resolved_model

    def _step(
        self, ctx: AssessContext, inputs: StepInputs, config: ProcessComplianceConfig
    ) -> StepAssessment:
        trajectory = _measurable(ctx, inputs)
        if isinstance(trajectory, StepAssessment):
            return trajectory
        events: tuple[TraceEvent, ...] = classify(trajectory, config.classifier())
        outcomes = run_checks(events)
        audit_settled(self._jev, inputs.step, outcomes)
        ambiguous = [outcome for outcome in outcomes if outcome.verdict is Verdict.AMBIGUOUS]
        jev, model = self._jev_observation(inputs.step, trajectory, ambiguous)
        decided = [
            outcome for outcome in outcomes if outcome.verdict in (Verdict.PASS, Verdict.FAIL)
        ]
        return StepAssessment(
            observations=(*(_check_observation(inputs.step, o) for o in outcomes), jev),
            evidence=_evidence(inputs, outcomes, trajectory),
            passed=sum(1 for outcome in decided if outcome.verdict is Verdict.PASS),
            decided=len(decided),
            jev_model=model,
        )

    def assess(self, ctx: AssessContext) -> AssessResult:
        self._awaiting = False
        try:
            config = ProcessComplianceConfig.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                PROCESS_COMPLIANCE, ctx, AssessStatus.ERROR, f"invalid config: {exc}"
            )
        before = unanswered_decisions(self._jev)
        steps = [(inputs, self._step(ctx, inputs, config)) for inputs in ctx.steps]
        self._awaiting = unanswered_decisions(self._jev) > before
        return _combine(steps, policy_modes(self._jev))


def _measurable(ctx: AssessContext, inputs: StepInputs) -> Trajectory | StepAssessment:
    """The step's trajectory, or its statused assessment when it cannot be measured."""
    if not inputs.reached:
        return _statused(inputs.step, AssessStatus.NOT_APPLICABLE, "checkpoint not reached")
    inventory = ctx.inventory(inputs.step)
    if inventory is not None and not inventory.eligible:
        detail = "; ".join(issue.detail for issue in inventory.invalid)
        return _statused(inputs.step, AssessStatus.INELIGIBLE, f"invalid snapshot: {detail}")
    try:
        trajectory = load_trajectory(inputs.trace_dir)
    except TrajectoryError as exc:
        return _statused(inputs.step, AssessStatus.UNAVAILABLE, f"trajectory unreadable: {exc}")
    if trajectory is None:
        return _statused(inputs.step, AssessStatus.UNAVAILABLE, "the step has no ATIF trajectory")
    return trajectory


def _combine(
    steps: Sequence[tuple[StepInputs, StepAssessment]], modes: dict[str, str]
) -> AssessResult:
    assessed = [step for _, step in steps]
    observations = [*chain.from_iterable(step.observations for step in assessed), _rate(steps)]
    models = sorted(filter(None, {step.jev_model for step in assessed}))
    measured = any(item.status is AssessStatus.OK for item in observations)
    return AssessResult(
        status=AssessStatus.OK if measured else AssessStatus.UNAVAILABLE,
        reason=None if measured else "no step has a decidable process check",
        observations=tuple(observations),
        evidence=tuple(chain.from_iterable(step.evidence for step in assessed)),
        provenance=Provenance(jev_model_resolved=",".join(models) or None, policy_modes=modes),
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


def process_compliance_scorer() -> ProcessComplianceScorer:
    """Registry factory for ``process-compliance@1``.

    The registry loads scorers with no arguments and has no assessment store
    to root a ``JevRuntime`` in, so this binds none: ambiguous checks are then
    ``unavailable`` and nothing is audited. ``process_compliance_scorer_for_store``
    binds one.
    """
    return ProcessComplianceScorer()


def process_compliance_scorer_for_store(
    store_root: Path, *, replay_only: bool = False, trial: str | None = None
) -> ProcessComplianceScorer:
    """The scorer with a ``JevRuntime`` rooted in an assessment store (audit and cache)."""
    return ProcessComplianceScorer(store_runtime(store_root, replay_only=replay_only, trial=trial))
