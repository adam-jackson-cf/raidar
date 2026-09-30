"""The Jev residue of ``process-compliance@1`` (plan G5, H2), via ``raidar.jev.process``.

Deterministic checks decide what the trace decides; Jev answers only the
residue. The scorer uses the WS-H interface as it stands, with no protocol,
question wording or policy of its own:

- every check a rule decided is audited with ``record_settled`` (so the audit
  covers every decision, deterministic or not);
- every ambiguous check is asked with ``ask_residue`` as a ``ResidueCase``,
  whose answer id is the Jev check name (``JEV_CHECKS`` maps the manifest's
  metric ids onto ``ProcessCheck``);
- the mode is the repo-versioned ``process-compliance.residue`` policy of the
  bound ``JevRuntime``, which also owns the cache, audit and model pin.

The step's ``jev_ambiguous_compliance`` is the lowest ``noul`` probability over
its ambiguous checks: the step complied only if it complied with each, and the
least likely check bounds that from above. Shadow observations are tier
``jev`` and never feed a ScorePolicy.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from raidar.jev.client import FailureReason
from raidar.jev.gating import jev_policy_modes
from raidar.jev.process import (
    PROCESS_POLICY,
    ProcessCheck,
    ResidueCase,
    ask_residue,
    record_settled,
)
from raidar.jev.process import EventKind as ResidueKind
from raidar.jev.process import TraceEvent as ResidueEvent
from raidar.jev.runtime import DecisionOutcome, JevRuntime
from raidar.jev.session import open_runtime
from raidar.scorers.process_compliance.checks import (
    PLAN_WRITTEN,
    TESTS_RUN_BEFORE_FINISH,
    CheckOutcome,
    Verdict,
)
from raidar.scorers.process_compliance.trace_events import EDIT_TOOLS, call_view, step_calls
from raidar.scorers.resource_usage.atif import AGENT_SOURCE, Trajectory

JEV_METRIC = "jev_ambiguous_compliance"
JEV_CHECKS = {
    TESTS_RUN_BEFORE_FINISH: ProcessCheck.TESTS_RUN_BEFORE_FINISH,
    PLAN_WRITTEN: ProcessCheck.PLAN_BEFORE_EDIT,
}


# Failures a later run can remove (a key, a cache entry, a working provider).
# The Assessment reports them with one fixed reason, so its bytes do not depend
# on why Jev had no answer this time (``--jev-replay-only``, a missing key);
# the store's Jev audit keeps the exact reason of every decision.
UNANSWERED_REASONS = frozenset(
    {
        FailureReason.MISSING_KEY,
        FailureReason.CACHE_MISS,
        FailureReason.NETWORK,
        FailureReason.TIMEOUT,
        FailureReason.HTTP_ERROR,
        FailureReason.CREDITS,
        FailureReason.INVALID_RESPONSE,
        FailureReason.MODEL_MISMATCH,
        FailureReason.INVALID_ANSWERS,
    }
)
UNANSWERED = "Jev has not answered this decision yet; the store's Jev audit records why"


def unanswered_decisions(runtime: JevRuntime | None) -> int:
    """Decisions ``runtime`` could not get answered for a reason a later run can remove."""
    if runtime is None:
        return 0
    failures = runtime.totals.failures
    return sum(failures.get(reason.value, 0) for reason in UNANSWERED_REASONS)


def store_runtime(store_root: Path, *, replay_only: bool, trial: str | None) -> JevRuntime:
    """A ``JevRuntime`` whose audit and cache live in an assessment store."""
    return open_runtime(store_root, replay_only=replay_only, trial=trial)


def policy_modes(runtime: JevRuntime | None) -> dict[str, str]:
    """``{jev metric: effective mode}`` for provenance; ``off`` when no runtime is bound."""
    if runtime is None:
        return {JEV_METRIC: "off"}
    return jev_policy_modes({JEV_METRIC: PROCESS_POLICY}, runtime.policies)


def residue_events(trajectory: Trajectory) -> tuple[ResidueEvent, ...]:
    """The step's agent messages and tool calls, in order, as Jev residue events."""
    return tuple(
        ResidueEvent(index, kind, text) for index, (kind, text) in enumerate(_items(trajectory))
    )


def _items(trajectory: Trajectory) -> Iterator[tuple[ResidueKind, str]]:
    for step in trajectory.steps:
        if step.source == AGENT_SOURCE and step.message.strip():
            yield ResidueKind.MESSAGE, step.message
        for call in step_calls(step):
            view = call_view(call)
            if call.name in EDIT_TOOLS:
                kind = ResidueKind.EDIT
            elif view.commands:
                kind = ResidueKind.COMMAND
            else:
                kind = ResidueKind.TOOL
            body = view.text or str(call.arguments)
            yield kind, f"{call.session} {call.tool}: {body}"


def audit_settled(runtime: JevRuntime | None, step: str, outcomes: Sequence[CheckOutcome]) -> None:
    """Audit each check a deterministic rule decided."""
    if runtime is None:
        return
    for outcome in outcomes:
        if outcome.verdict in (Verdict.PASS, Verdict.FAIL):
            record_settled(
                runtime,
                step=step,
                check=JEV_CHECKS[outcome.check],
                rule=f"trace:{outcome.check}: {outcome.reason}",
                complied=outcome.verdict is Verdict.PASS,
            )


@dataclass(frozen=True)
class ResidueAnswer:
    """The step's Jev probability, or why there is none, and the model that answered."""

    value: float | None
    reason: str | None
    resolved_model: str | None = None


def _failure(outcome: DecisionOutcome) -> str:
    record = outcome.record
    if record.failure_reason in {reason.value for reason in UNANSWERED_REASONS}:
        return UNANSWERED
    if record.error or record.failure_reason:
        return f"Jev decision failed: {record.failure_reason or ''} {record.error or ''}".strip()
    return f"Jev decision {record.verdict} under the {record.mode.value} policy"


def ask(
    runtime: JevRuntime, step: str, trajectory: Trajectory, ambiguous: Sequence[CheckOutcome]
) -> ResidueAnswer:
    """Ask Jev about each ambiguous check of one step and combine the answers."""
    events = residue_events(trajectory)
    values: list[float] = []
    models: set[str] = set()
    for outcome in ambiguous:
        check = JEV_CHECKS[outcome.check]
        decision = ask_residue(runtime, ResidueCase(step, check, events, outcome.reason))
        if decision.record.resolved_model:
            models.add(decision.record.resolved_model)
        value = decision.noul(check.value)
        if value is None:
            return ResidueAnswer(None, _failure(decision), ",".join(sorted(models)) or None)
        values.append(value)
    return ResidueAnswer(min(values), None, ",".join(sorted(models)) or None)
