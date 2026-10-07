"""The step's work session and whether it ran tests itself (``process-compliance@5``).

The work session is the session that received the step's first user prompt. A
workflow wrapper (a commit gate, a review loop) starts its own sessions after it,
and their test runs are the wrapper's, not the agent's: in ``smoke-3b-2`` the
gate's fix sessions ran ``guardrail-check`` (which runs the tests) at every r2
step, so the step-wide ``tests_run_before_finish`` passed whatever the work
session did. A trajectory with no user prompt is read as one session.
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scorers.process_checks.checks import (
    TESTS_RUN_BEFORE_FINISH,
    CheckOutcome,
    tests_run_before_finish,
)
from raidar.scorers.process_checks.core import check_observation
from raidar.scorers.process_checks.scorer import UNDETERMINED
from raidar.scorers.process_checks.trace_events import TraceEvent
from raidar.scorers.resource_usage.atif import Trajectory
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)
from raidar.scorers.sdk.result import TRAJECTORY_SUBJECT, AssessStatus, Observation

WORK_TESTS = f"work_session_{TESTS_RUN_BEFORE_FINISH}"
WORK_TESTS_RATE = "work_session_tests_run_rate"
USER_SOURCE = "user"
_OK = AssessStatus.OK

WORK_SESSION_METRICS = (
    MetricSpec(
        id=WORK_TESTS,
        description=(
            "The step's work session (the one given the step's first prompt) ran the project's "
            "tests after its last source edit; a test command counts only if it ran."
        ),
        level=MetricLevel.CHECKPOINT,
        type=MetricType.BOOLEAN,
        direction=Direction.HIGHER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
    MetricSpec(
        id=WORK_TESTS_RATE,
        description="Share of decided steps whose work session ran tests after its last edit.",
        level=MetricLevel.TRAJECTORY,
        type=MetricType.RATIO,
        direction=Direction.HIGHER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
)


def work_session(trajectory: Trajectory) -> str | None:
    """The session that received the step's first user prompt (None: no session did)."""
    return next((step.session for step in trajectory.steps if step.source == USER_SOURCE), None)


def work_events(events: Sequence[TraceEvent], session: str | None) -> tuple[TraceEvent, ...]:
    """The work session's events (every event when no session received a prompt)."""
    if session is None:
        return tuple(events)
    return tuple(event for event in events if event.session == session)


def check_observations(step: str, outcomes: Sequence[CheckOutcome]) -> tuple[Observation, ...]:
    """@2's observation of each check (an undecided one is ``undetermined``)."""
    return tuple(check_observation(step, outcome, UNDETERMINED) for outcome in outcomes)


def work_observation(
    step: str, events: Sequence[TraceEvent], trajectory: Trajectory
) -> Observation:
    """``work_session_tests_run_before_finish`` over the work session's events."""
    outcome = tests_run_before_finish(work_events(events, work_session(trajectory)))
    (observation,) = check_observations(step, (outcome,))
    return observation.model_copy(update={"metric": WORK_TESTS})


def work_rate(observations: Sequence[Observation]) -> Observation:
    """Passed over decided steps (unavailable when a step could not be read)."""
    per_step = [item for item in observations if item.metric == WORK_TESTS]
    gaps = [item.subject for item in per_step if item.status is AssessStatus.UNAVAILABLE]
    if gaps:
        return Observation(
            metric=WORK_TESTS_RATE,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=f"{WORK_TESTS} is unavailable at {', '.join(gaps)}",
        )
    decided = [item.value or 0.0 for item in per_step if item.status is _OK]
    if not decided:
        return Observation(
            metric=WORK_TESTS_RATE,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="no step's work session decided the test-run check",
        )
    passed = float(sum(decided))
    return Observation(
        metric=WORK_TESTS_RATE,
        subject=TRAJECTORY_SUBJECT,
        status=_OK,
        value=passed / len(decided),
        numerator=passed,
        denominator=float(len(decided)),
    )


__all__ = [
    "WORK_SESSION_METRICS",
    "WORK_TESTS",
    "WORK_TESTS_RATE",
    "check_observations",
    "work_events",
    "work_observation",
    "work_rate",
    "work_session",
]
