"""``process-compliance@2``: deterministic trace checks only (plan G5; F7 change 2026-10-04).

For each reached checkpoint the step's trajectory is classified into process
events and two checks run over them:

- ``tests_run_before_finish``: tests ran after the last source edit;
- ``plan_written``: a plan tool or plan file came before the first edit.

A decided check is ``ok`` (1 pass, 0 fail) with evidence at the deciding tool
call. A step with no source edit is ``not_applicable``. A check the trace
cannot decide is **undetermined**: status ``insufficient_evidence`` with an
``undetermined: <why>`` reason and no value, so it is never a zero and never
enters ``compliance_rate``. No model is asked: model judgement is used only
when a needed measure cannot be made deterministically, and none qualifies
(requirements change log, 2026-10-04). This package imports nothing from
``raidar.jev`` (an import-linter contract enforces it).

``compliance_rate`` is passed / decided checks over the eligible reached
checkpoints; it is ``unavailable`` when a reached step has no readable
trajectory.
"""

from __future__ import annotations

from pydantic import ValidationError

from raidar.scorers.process_checks.checks import run_checks
from raidar.scorers.process_checks.core import (
    ProcessComplianceConfig,
    StepAssessment,
    check_observation,
    combine,
    decided_tally,
    measurable,
    step_evidence,
)
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks.trace_events import classify
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Provenance

UNDETERMINED = "undetermined"
_CHECKPOINT_METRICS = tuple(
    metric.id for metric in PROCESS_COMPLIANCE_V2.metrics if metric.level is MetricLevel.CHECKPOINT
)


class DeterministicProcessComplianceScorer:
    """``process-compliance@2``: decides only what the trace decides."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE_V2

    def _step(
        self, ctx: AssessContext, inputs: StepInputs, config: ProcessComplianceConfig
    ) -> StepAssessment:
        trajectory = measurable(ctx, inputs, _CHECKPOINT_METRICS)
        if isinstance(trajectory, StepAssessment):
            return trajectory
        outcomes = run_checks(classify(trajectory, config.classifier()))
        passed, decided = decided_tally(outcomes)
        return StepAssessment(
            observations=tuple(
                check_observation(inputs.step, outcome, UNDETERMINED) for outcome in outcomes
            ),
            evidence=step_evidence(inputs, outcomes, trajectory),
            passed=passed,
            decided=decided,
        )

    def assess(self, ctx: AssessContext) -> AssessResult:
        try:
            config = ProcessComplianceConfig.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                PROCESS_COMPLIANCE_V2, ctx, AssessStatus.ERROR, f"invalid config: {exc}"
            )
        steps = [(inputs, self._step(ctx, inputs, config)) for inputs in ctx.steps]
        return combine(steps, Provenance())


def deterministic_process_compliance_scorer() -> DeterministicProcessComplianceScorer:
    """Registry factory for ``process-compliance@2``."""
    return DeterministicProcessComplianceScorer()
