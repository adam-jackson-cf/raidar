"""One step of ``process-compliance@6``: @5's step with the two work-session test checks."""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scorers.process_checks.checks import run_checks
from raidar.scorers.process_checks.core import StepAssessment, measurable, step_evidence
from raidar.scorers.process_checks.trace_events import classify
from raidar.scorers.process_checks_v4.scorer import NOT_RUN, call_outputs, split_unrun
from raidar.scorers.process_checks_v5.scorer import ProcessComplianceV5Config, _counted
from raidar.scorers.process_checks_v5.work_session import check_observations, work_observation
from raidar.scorers.process_checks_v6.work_tests import work_tests_observations
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.result import AssessStatus, Observation


def assess_step(
    ctx: AssessContext,
    inputs: StepInputs,
    config: ProcessComplianceV5Config,
    checkpoint_metrics: Sequence[str],
) -> StepAssessment:
    """@5's observations of the step, plus ``work_session_tests_run_any`` and
    ``work_session_tests_after_last_code_edit``."""
    trajectory = measurable(ctx, inputs, tuple(checkpoint_metrics))
    if isinstance(trajectory, StepAssessment):
        return trajectory
    events = classify(trajectory, config.classifier())
    split = split_unrun(events, call_outputs(trajectory.path))
    outcomes = run_checks(split.kept)
    passed, decided = _counted(outcomes, config.plan_in_compliance_rate)
    not_run = Observation(
        metric=NOT_RUN, subject=inputs.step, status=AssessStatus.OK, value=len(split.not_run)
    )
    return StepAssessment(
        observations=(
            *check_observations(inputs.step, outcomes),
            not_run,
            work_observation(inputs.step, split.kept, trajectory),
            *work_tests_observations(inputs.step, split.kept, trajectory),
        ),
        evidence=step_evidence(inputs, outcomes, trajectory),
        passed=passed,
        decided=decided,
    )


__all__ = ["assess_step"]
