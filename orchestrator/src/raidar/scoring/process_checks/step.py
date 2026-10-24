"""One step of ``process-compliance``: the trace checks and the work session's test checks."""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scoring.framework.context import AssessContext, StepInputs
from raidar.scoring.framework.result import AssessStatus, Observation
from raidar.scoring.process_checks.assess import (
    ProcessComplianceConfig,
    StepAssessment,
    measurable,
    step_evidence,
)
from raidar.scoring.process_checks.checks import (
    PLAN_WRITTEN,
    TESTS_RUN_BEFORE_FINISH,
    CheckOutcome,
    Verdict,
    run_checks,
)
from raidar.scoring.process_checks.outputs import NOT_RUN, call_outputs, split_unrun
from raidar.scoring.process_checks.trace_events import classify
from raidar.scoring.process_checks.work_session import check_observations, work_observation
from raidar.scoring.process_checks.work_tests import work_tests_observations


def _counted(outcomes: Sequence[CheckOutcome], plan: bool) -> tuple[int, int]:
    """(passed, decided) over the checks the compliance rate counts."""
    rated = {TESTS_RUN_BEFORE_FINISH, *((PLAN_WRITTEN,) if plan else ())}
    decided = [
        outcome
        for outcome in outcomes
        if outcome.check in rated and outcome.verdict in (Verdict.PASS, Verdict.FAIL)
    ]
    return sum(1 for outcome in decided if outcome.verdict is Verdict.PASS), len(decided)


def assess_step(
    ctx: AssessContext,
    inputs: StepInputs,
    config: ProcessComplianceConfig,
    checkpoint_metrics: Sequence[str],
) -> StepAssessment:
    """The step's checks, its not-run test commands, and its work session's test checks."""
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
