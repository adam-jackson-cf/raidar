"""``process-compliance@1``: deterministic trace checks plus a Jev shadow (plan G5).

Dormant since 2026-10-04 (Jev parked; requirements change log, F7). New
experiments declare ``process-compliance@2`` (``raidar.scorers.process_checks``),
which shares this version's deterministic core and has no Jev metric. ``@1``
stays registered so its stored Assessments and locks stay readable; its Jev
policy ``process-compliance.residue`` is now ``off``, so a rescore never calls
Jev and ``jev_ambiguous_compliance`` is ``unavailable``.

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

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from raidar.scorers.builtin_manifests import PROCESS_COMPLIANCE
from raidar.scorers.process_checks.checks import CheckOutcome, Verdict, run_checks
from raidar.scorers.process_checks.core import (
    ProcessComplianceConfig,
    StepAssessment,
    check_observation,
    combine,
    decided_tally,
    measurable,
    step_evidence,
)
from raidar.scorers.process_checks.trace_events import TraceEvent, classify
from raidar.scorers.process_compliance.jev_residue import (
    JEV_METRIC,
    JevRuntime,
    ask,
    audit_settled,
    policy_modes,
    store_runtime,
    unanswered_decisions,
)
from raidar.scorers.resource_usage.atif import Trajectory
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, Provenance

_CHECKPOINT_METRICS = tuple(
    metric.id for metric in PROCESS_COMPLIANCE.metrics if metric.level is MetricLevel.CHECKPOINT
)
_AMBIGUOUS = "ambiguous"


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
        trajectory = measurable(ctx, inputs, _CHECKPOINT_METRICS)
        if isinstance(trajectory, StepAssessment):
            return trajectory
        events: tuple[TraceEvent, ...] = classify(trajectory, config.classifier())
        outcomes = run_checks(events)
        audit_settled(self._jev, inputs.step, outcomes)
        ambiguous = [outcome for outcome in outcomes if outcome.verdict is Verdict.AMBIGUOUS]
        jev, model = self._jev_observation(inputs.step, trajectory, ambiguous)
        passed, decided = decided_tally(outcomes)
        return StepAssessment(
            observations=(
                *(check_observation(inputs.step, o, _AMBIGUOUS) for o in outcomes),
                jev,
            ),
            evidence=step_evidence(inputs, outcomes, trajectory),
            passed=passed,
            decided=decided,
            resolved_model=model,
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


def _combine(
    steps: Sequence[tuple[StepInputs, StepAssessment]], modes: dict[str, str]
) -> AssessResult:
    models = sorted(filter(None, {step.resolved_model for _, step in steps}))
    provenance = Provenance(jev_model_resolved=",".join(models) or None, policy_modes=modes)
    return combine(steps, provenance)


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
