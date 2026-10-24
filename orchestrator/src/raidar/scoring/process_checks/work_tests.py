"""The work session's test runs, read two more ways.

``work_session_tests_run_before_finish`` passes only when a test run follows
the work session's last edit. In ``smoke-3b-3`` of the guardrails experiment it
read 0 in both arms, although r1's work sessions never ran a test and r2's ran
them at four steps and then made one more edit. Two checks tell those apart,
over the same work-session events (a test command counts only when it ran):

- ``work_session_tests_run_any``: the work session ran the tests at all.
- ``work_session_tests_after_last_code_edit``: a test run follows the work
  session's last edit to a file that is not a test file (``test_*.py``,
  ``*_test.py``, ``conftest.py``, or a file under a ``tests``/``test``
  directory: the gate's test-file globs). Adding a test after the last run
  does not fail it; an unparsed write after the last run leaves it undetermined.

Both are ``not_applicable`` when the work session made no source edit, and the
second also when it edited test files only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from raidar.scoring.framework.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    Tier,
)
from raidar.scoring.framework.result import TRAJECTORY_SUBJECT, AssessStatus, Observation
from raidar.scoring.process_checks.checks import (
    CheckOutcome,
    Verdict,
    tests_run_before_finish,
)
from raidar.scoring.process_checks.trace_events import EventKind, TraceEvent
from raidar.scoring.process_checks.work_session import (
    check_observations,
    work_events,
    work_rate,
    work_session,
)
from raidar.scoring.resource_usage.atif import Trajectory

WORK_TESTS_ANY = "work_session_tests_run_any"
WORK_TESTS_AFTER_CODE = "work_session_tests_after_last_code_edit"
WORK_CHECKS = (WORK_TESTS_ANY, WORK_TESTS_AFTER_CODE)
TEST_DIRECTORIES = frozenset({"tests", "test"})
NO_EDIT = "no source edit in the work session"
TESTS_ONLY = "the work session edited test files only"
_OK = AssessStatus.OK


def _check(metric: str, description: str) -> MetricSpec:
    return MetricSpec(
        id=metric,
        description=description,
        level=MetricLevel.CHECKPOINT,
        type=MetricType.BOOLEAN,
        direction=Direction.HIGHER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    )


def _rate(metric: str, description: str) -> MetricSpec:
    return MetricSpec(
        id=f"{metric}_rate",
        description=description,
        level=MetricLevel.TRAJECTORY,
        type=MetricType.RATIO,
        direction=Direction.HIGHER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    )


WORK_TESTS_METRICS = (
    _check(
        WORK_TESTS_ANY,
        "The step's work session ran the project's tests at least once (a test command counts "
        "only if it ran); not applicable when it made no source edit.",
    ),
    _check(
        WORK_TESTS_AFTER_CODE,
        "The step's work session ran the project's tests after its last edit to a non-test "
        "file (edits to test files after the run do not fail it).",
    ),
    _rate(WORK_TESTS_ANY, "Share of decided steps whose work session ran the tests at all."),
    _rate(
        WORK_TESTS_AFTER_CODE,
        "Share of decided steps whose work session ran the tests after its last code edit.",
    ),
)


def is_test_file(relative: str) -> bool:
    """A workspace path the gate's test-file globs cover."""
    path = PurePosixPath(relative)
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    return any(part in TEST_DIRECTORIES for part in path.parts[:-1])


def _edited(events: Sequence[TraceEvent]) -> bool:
    return any(event.kind in (EventKind.EDIT, EventKind.AMBIGUOUS_EDIT) for event in events)


def tests_run_any(events: Sequence[TraceEvent]) -> CheckOutcome:
    """Pass when any test run is in the events (not applicable without a source edit)."""
    if not _edited(events):
        return CheckOutcome(WORK_TESTS_ANY, Verdict.NOT_APPLICABLE, NO_EDIT)
    runs = tuple(event for event in events if event.kind is EventKind.TEST)
    if runs:
        return CheckOutcome(WORK_TESTS_ANY, Verdict.PASS, "the work session ran tests", runs[:1])
    return CheckOutcome(WORK_TESTS_ANY, Verdict.FAIL, "the work session ran no test")


def tests_after_last_code_edit(events: Sequence[TraceEvent]) -> CheckOutcome:
    """``tests_run_before_finish`` over the work session's events less the test-file edits."""
    if not _edited(events):
        return CheckOutcome(WORK_TESTS_AFTER_CODE, Verdict.NOT_APPLICABLE, NO_EDIT)
    code = tuple(
        event
        for event in events
        if not (event.kind is EventKind.EDIT and event.path and is_test_file(event.path))
    )
    if not _edited(code):
        return CheckOutcome(WORK_TESTS_AFTER_CODE, Verdict.NOT_APPLICABLE, TESTS_ONLY)
    outcome = tests_run_before_finish(code)
    return CheckOutcome(WORK_TESTS_AFTER_CODE, outcome.verdict, outcome.reason, outcome.events)


def work_tests_observations(
    step: str, events: Sequence[TraceEvent], trajectory: Trajectory
) -> tuple[Observation, ...]:
    """Both checks over the step's work-session events."""
    work = work_events(events, work_session(trajectory))
    return check_observations(step, (tests_run_any(work), tests_after_last_code_edit(work)))


def work_tests_rate(metric: str, observations: Sequence[Observation]) -> Observation:
    """Passed over decided steps (unavailable when a step could not be read)."""
    name = f"{metric}_rate"
    per_step = [item for item in observations if item.metric == metric]
    gaps = [item.subject for item in per_step if item.status is AssessStatus.UNAVAILABLE]
    if gaps:
        return Observation(
            metric=name,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=f"{metric} is unavailable at {', '.join(gaps)}",
        )
    decided = [item.value or 0.0 for item in per_step if item.status is _OK]
    if not decided:
        return Observation(
            metric=name,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason=f"no step decided {metric}",
        )
    passed = float(sum(decided))
    return Observation(
        metric=name,
        subject=TRAJECTORY_SUBJECT,
        status=_OK,
        value=passed / len(decided),
        numerator=passed,
        denominator=float(len(decided)),
    )


def trajectory_rates(observations: Sequence[Observation]) -> tuple[Observation, ...]:
    """The rate of every work-session test check."""
    return (
        work_rate(observations),
        *(work_tests_rate(metric, observations) for metric in WORK_CHECKS),
    )


__all__ = [
    "WORK_CHECKS",
    "WORK_TESTS_AFTER_CODE",
    "WORK_TESTS_ANY",
    "WORK_TESTS_METRICS",
    "is_test_file",
    "tests_after_last_code_edit",
    "tests_run_any",
    "trajectory_rates",
    "work_tests_observations",
    "work_tests_rate",
]
