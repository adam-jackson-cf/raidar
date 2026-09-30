"""Process-compliance residue questions: the interface ``process-compliance@1`` uses.

Deterministic trace parsing comes first (design memo §3.5, plan G5); Jev
answers only the ambiguous residue. The scorer
(``raidar.scorers.process_compliance.jev_residue``):

1. settles every case it can with a rule and audits it with
   ``record_settled`` (so every decision, deterministic or not, is audited);
2. hands each ambiguous case to ``ask_residue`` as a ``ResidueCase``, with the
   normalised trace events of that step and a one-line reason the rule could
   not decide;
3. reads ``DecisionOutcome.noul(case.check)`` as the probability the step
   complied, and ``DecisionOutcome.counts_toward_verdicts``, which is false in
   shadow, to decide whether it may feed anything but an uncalibrated metric.

Events are clipped per event, and when the step's events exceed the budget
the head and the tail are kept (plans happen early, final test runs late). Any
clipping makes the input ``partial``; a step with no events is ``missing`` and
is not asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from raidar.jev.audit import DecisionRecord, InputSufficiency, SufficiencyLevel
from raidar.jev.protocol import NoulCriteria, NoulQuestion
from raidar.jev.redaction import clip
from raidar.jev.runtime import DecisionOutcome, JevRuntime

PROCESS_POLICY = "process-compliance.residue"
EVENT_CHARS = 2_000
EVENTS_BUDGET_CHARS = 48_000
INSTRUCTION_CHARS = 8_000


class ProcessCheck(StrEnum):
    PLAN_BEFORE_EDIT = "plan_before_edit"
    TESTS_RUN_BEFORE_FINISH = "tests_run_before_finish"
    TESTS_FIRST = "tests_first"


class EventKind(StrEnum):
    MESSAGE = "message"
    COMMAND = "command"
    EDIT = "edit"
    TOOL = "tool"


QUESTIONS: dict[ProcessCheck, NoulQuestion] = {
    ProcessCheck.PLAN_BEFORE_EDIT: NoulQuestion(
        instructions=(
            "Did the agent write or state a concrete plan for this step before its first "
            "edit to source code? Use only `events`, which are in order."
        ),
        criteria=NoulCriteria(
            true="A plan with concrete steps appears in a message, a plan file or a todo "
            "list before the first source edit.",
            false="No plan appears, or it appears only after source edits began, or it is a "
            "restatement of the task with no steps.",
        ),
    ),
    ProcessCheck.TESTS_RUN_BEFORE_FINISH: NoulQuestion(
        instructions=(
            "After its last edit to source code in this step, did the agent run the "
            "project's test suite (not a single ad-hoc check) before finishing?"
        ),
        criteria=NoulCriteria(
            true="A test command covering the suite runs after the last source edit.",
            false="No test command runs after the last source edit, or only an unrelated or "
            "single-case command runs.",
        ),
    ),
    ProcessCheck.TESTS_FIRST: NoulQuestion(
        instructions=(
            "Did the agent write or change tests for this step's behaviour before the "
            "implementation code those tests cover?"
        ),
        criteria=NoulCriteria(
            true="Test edits for the new behaviour precede the implementation edits.",
            false="Implementation edits come first, or no tests for the new behaviour are written.",
        ),
    ),
}


@dataclass(frozen=True)
class TraceEvent:
    index: int
    kind: EventKind
    text: str
    path: str | None = None


@dataclass(frozen=True)
class ResidueCase:
    """One ambiguous check for one step, as the deterministic scorer left it."""

    step: str
    check: ProcessCheck
    events: tuple[TraceEvent, ...]
    ambiguity: str
    instruction: str | None = None

    @property
    def subject(self) -> str:
        return f"{self.step}:{self.check.value}"


def _event(event: TraceEvent) -> tuple[dict[str, Any], bool]:
    text = clip(event.text, EVENT_CHARS)
    rendered: dict[str, Any] = {"index": event.index, "kind": event.kind.value, "text": text.text}
    if event.path is not None:
        rendered["path"] = event.path
    return rendered, text.clipped


def _select(rendered: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Head and tail events within the budget, and how many were omitted."""
    sizes = [len(item["text"]) for item in rendered]
    if sum(sizes) <= EVENTS_BUDGET_CHARS:
        return rendered, 0
    half = EVENTS_BUDGET_CHARS // 2
    head: list[dict[str, Any]] = []
    used = 0
    for item, size in zip(rendered, sizes, strict=True):
        if used + size > half:
            break
        head.append(item)
        used += size
    tail: list[dict[str, Any]] = []
    used = 0
    for item, size in zip(
        reversed(rendered[len(head) :]), reversed(sizes[len(head) :]), strict=True
    ):
        if used + size > half:
            break
        tail.insert(0, item)
        used += size
    omitted = len(rendered) - len(head) - len(tail)
    return [*head, {"omitted_events": omitted}, *tail], omitted


def residue_state(case: ResidueCase) -> tuple[dict[str, Any], InputSufficiency]:
    if not case.events:
        return {"step": case.step, "check": case.check.value}, InputSufficiency(
            level=SufficiencyLevel.MISSING, missing=("trace",)
        )
    pairs = [_event(event) for event in sorted(case.events, key=lambda item: item.index)]
    events, omitted = _select([rendered for rendered, _ in pairs])
    clipped = ["events"] if omitted or any(was_clipped for _, was_clipped in pairs) else []
    state: dict[str, Any] = {
        "step": case.step,
        "check": case.check.value,
        "why_ambiguous": case.ambiguity,
        "events": events,
    }
    if case.instruction is not None:
        instruction = clip(case.instruction, INSTRUCTION_CHARS)
        state["instruction"] = instruction.text
        clipped += ["instruction"] if instruction.clipped else []
    level = SufficiencyLevel.PARTIAL if clipped else SufficiencyLevel.SUFFICIENT
    return state, InputSufficiency(level=level, clipped=tuple(clipped))


def ask_residue(
    runtime: JevRuntime, case: ResidueCase, *, policy_id: str = PROCESS_POLICY
) -> DecisionOutcome:
    """Ask Jev about one ambiguous case; the answer id is ``case.check``."""
    state, sufficiency = residue_state(case)
    return runtime.decide(
        policy_id,
        subject=case.subject,
        state=state,
        questions={case.check.value: QUESTIONS[case.check]},
        sufficiency=sufficiency,
    )


def record_settled(
    runtime: JevRuntime,
    *,
    step: str,
    check: ProcessCheck,
    rule: str,
    complied: bool,
    policy_id: str = PROCESS_POLICY,
) -> DecisionRecord:
    """Audit a case a deterministic rule settled, so the audit covers every decision."""
    return runtime.record_deterministic(
        policy_id,
        subject=f"{step}:{check.value}",
        rule=rule,
        verdict="complied" if complied else "not_complied",
    )
