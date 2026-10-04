"""Deterministic process checks over one step's classified trace events.

Each check returns a ``CheckOutcome``: ``pass`` or ``fail`` when the trace
decides it, ``ambiguous`` when an ambiguous event could change the answer
(``process-compliance@2`` records these as undetermined; the dormant ``@1``
also hands them to its Jev residue), and ``not_applicable`` when the step made no
source edit, so neither rule has anything to order against.

Order is the position of an event in the classified sequence; one call can
yield several events (``cat > a.py && pytest`` is an edit, then a test run).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from raidar.scorers.process_checks.trace_events import EventKind, TraceEvent

TESTS_RUN_BEFORE_FINISH = "tests_run_before_finish"
PLAN_WRITTEN = "plan_written"
CHECK_RULES = {
    TESTS_RUN_BEFORE_FINISH: "run the project's tests after its last source edit in the step",
    PLAN_WRITTEN: "write a plan before its first source edit in the step",
}


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CheckOutcome:
    check: str
    verdict: Verdict
    reason: str
    events: tuple[TraceEvent, ...] = field(default=())


@dataclass(frozen=True)
class _Timeline:
    """Positions of each event kind in one step's classified sequence."""

    events: Sequence[TraceEvent]

    def at(self, *kinds: EventKind) -> list[int]:
        return [position for position, event in enumerate(self.events) if event.kind in kinds]

    def pick(self, positions: Sequence[int]) -> tuple[TraceEvent, ...]:
        return tuple(self.events[position] for position in positions)

    @property
    def edited(self) -> bool:
        return bool(self.at(EventKind.EDIT, EventKind.AMBIGUOUS_EDIT))


_NO_EDIT = "no source edit in the step"


def tests_run_before_finish(events: Sequence[TraceEvent]) -> CheckOutcome:
    """Pass when a test run follows every source edit, definite or ambiguous."""
    check, timeline = TESTS_RUN_BEFORE_FINISH, _Timeline(events)
    if not timeline.edited:
        return CheckOutcome(check, Verdict.NOT_APPLICABLE, _NO_EDIT)
    last_test = max(timeline.at(EventKind.TEST), default=-1)
    last_edit = max(timeline.at(EventKind.EDIT), default=-1)
    later = [p for p in timeline.at(EventKind.AMBIGUOUS_EDIT) if p > last_test]
    if last_test > last_edit and not later:
        return CheckOutcome(
            check, Verdict.PASS, "tests ran after the last edit", timeline.pick([last_test])
        )
    if last_test < last_edit:
        edit = events[last_edit].detail
        return CheckOutcome(
            check, Verdict.FAIL, f"no test run after source edit {edit}", timeline.pick([last_edit])
        )
    return CheckOutcome(
        check,
        Verdict.AMBIGUOUS,
        "an unparsed write follows the last test run",
        timeline.pick(later),
    )


def plan_written(events: Sequence[TraceEvent]) -> CheckOutcome:
    """Pass when a plan tool or plan file precedes the first source edit."""
    check, timeline = PLAN_WRITTEN, _Timeline(events)
    if not timeline.edited:
        return CheckOutcome(check, Verdict.NOT_APPLICABLE, _NO_EDIT)
    first_edit = min(timeline.at(EventKind.EDIT), default=len(events))
    first_any = min(timeline.at(EventKind.EDIT, EventKind.AMBIGUOUS_EDIT))
    plans = timeline.at(EventKind.PLAN)[:1]
    if plans and plans[0] < first_any:
        return CheckOutcome(check, Verdict.PASS, "plan before the first edit", timeline.pick(plans))
    candidates = [p for p in [*timeline.at(EventKind.AMBIGUOUS_PLAN), *plans] if p < first_edit]
    if not candidates and first_edit < len(events):
        edit = events[first_edit].detail
        return CheckOutcome(
            check, Verdict.FAIL, f"no plan before source edit {edit}", timeline.pick([first_edit])
        )
    return CheckOutcome(
        check,
        Verdict.AMBIGUOUS,
        "a plan precedes the first definite edit only as listed steps or after an unparsed write",
        timeline.pick(candidates),
    )


def run_checks(events: Sequence[TraceEvent]) -> tuple[CheckOutcome, ...]:
    return (tests_run_before_finish(events), plan_written(events))
