"""``process-compliance@5``: process-compliance@4 measuring the agent, not the gate around it.

``process-compliance@4`` (``raidar.scorers.process_checks_v4``) is released and
unchanged. In ``smoke-3b-2`` of the guardrails experiment its
``compliance_rate`` mostly measured whether a gate was present: ``plan_written``
failed at every step in both arms although neither condition asks for a plan
(a constant that halved the rate), and the treatment arm's
``tests_run_before_finish`` passed at every step only because the gate's fix
sessions ran ``guardrail-check``. @5 keeps every @4 metric, value, status and
reason except ``compliance_rate``, and adds one check:

- ``work_session_tests_run_before_finish`` (checkpoint): @4's
  ``tests_run_before_finish`` (a test command counts only when it ran) over the
  step's **work session** only: the session that received the step's first
  user prompt, before any session a wrapper starts after it (a commit gate's
  fix sessions, for example). ``not_applicable`` when the work session made no
  source edit; undetermined as @4's check is. ``work_session_tests_run_rate``
  (trajectory): passed over decided steps (``not_applicable`` when none
  decided). In an arm without such wrappers it equals the step-wide check.
- ``compliance_rate`` counts ``tests_run_before_finish`` only, unless the
  assessment says the condition asks for a plan (scorer config
  ``plan_in_compliance_rate: true``): then ``plan_written`` counts too, as in
  @2-@4. ``plan_written`` itself is still reported at every step.

Everything stays deterministic: no model is asked.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from raidar.scorers.process_checks.checks import (
    PLAN_WRITTEN,
    TESTS_RUN_BEFORE_FINISH,
    CheckOutcome,
    Verdict,
    run_checks,
)
from raidar.scorers.process_checks.core import (
    ProcessComplianceConfig,
    StepAssessment,
    combine,
    measurable,
    step_evidence,
)
from raidar.scorers.process_checks.trace_events import classify
from raidar.scorers.process_checks_v3.scorer import STALL_METRICS, stall_observations
from raidar.scorers.process_checks_v4.scorer import (
    NOT_RUN,
    PROCESS_COMPLIANCE_V4,
    _total,
    call_outputs,
    split_unrun,
)
from raidar.scorers.process_checks_v5.work_session import (
    WORK_SESSION_METRICS,
    check_observations,
    work_observation,
    work_rate,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs, statused_result
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Observation, Provenance

_OK = AssessStatus.OK
PROCESS_COMPLIANCE_V5 = ScorerManifest(
    id="process-compliance",
    version="5",
    description=(
        "Deterministic trace checks of agent process (process-compliance@4: stall outcomes, "
        "a test command counts only when it ran); a check the trace cannot decide is "
        "undetermined, never judged by a model. Adds whether the step's work session itself "
        "ran tests after its last edit; the compliance rate counts the test-run check, and "
        "the plan check only when the condition asks for a plan."
    ),
    runtimes=PROCESS_COMPLIANCE_V4.runtimes,
    evidence_required=PROCESS_COMPLIANCE_V4.evidence_required,
    metrics=(*PROCESS_COMPLIANCE_V4.metrics, *WORK_SESSION_METRICS),
)
_CHECKPOINT_METRICS = tuple(
    metric.id
    for metric in PROCESS_COMPLIANCE_V5.metrics
    if metric.level is MetricLevel.CHECKPOINT and metric not in STALL_METRICS
)


class ProcessComplianceV5Config(ProcessComplianceConfig):
    """@4's config, plus whether the condition asks for a plan (``plan_written`` in the rate)."""

    plan_in_compliance_rate: bool = False


def _counted(outcomes: Sequence[CheckOutcome], plan: bool) -> tuple[int, int]:
    """(passed, decided) over the checks the compliance rate counts."""
    counted = {TESTS_RUN_BEFORE_FINISH, *((PLAN_WRITTEN,) if plan else ())}
    decided = [
        outcome
        for outcome in outcomes
        if outcome.check in counted and outcome.verdict in (Verdict.PASS, Verdict.FAIL)
    ]
    return sum(1 for outcome in decided if outcome.verdict is Verdict.PASS), len(decided)


# ---- the scorer -------------------------------------------------------------------------


class ProcessComplianceV5Scorer:
    """process-compliance@4 with the work session's own test runs and a gate-free rate."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE_V5

    def _step(
        self, ctx: AssessContext, inputs: StepInputs, config: ProcessComplianceV5Config
    ) -> StepAssessment:
        trajectory = measurable(ctx, inputs, _CHECKPOINT_METRICS)
        if isinstance(trajectory, StepAssessment):
            return trajectory
        events = classify(trajectory, config.classifier())
        split = split_unrun(events, call_outputs(trajectory.path))
        outcomes = run_checks(split.kept)
        passed, decided = _counted(outcomes, config.plan_in_compliance_rate)
        not_run = Observation(
            metric=NOT_RUN, subject=inputs.step, status=_OK, value=len(split.not_run)
        )
        return StepAssessment(
            observations=(
                *check_observations(inputs.step, outcomes),
                not_run,
                work_observation(inputs.step, split.kept, trajectory),
            ),
            evidence=step_evidence(inputs, outcomes, trajectory),
            passed=passed,
            decided=decided,
        )

    def assess(self, ctx: AssessContext) -> AssessResult:
        try:
            config = ProcessComplianceV5Config.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                PROCESS_COMPLIANCE_V5, ctx, AssessStatus.ERROR, f"invalid config: {exc}"
            )
        steps = [(inputs, self._step(ctx, inputs, config)) for inputs in ctx.steps]
        base = combine(steps, Provenance())
        observations = (
            *base.observations,
            *stall_observations(ctx),
            _total(base.observations),
            work_rate(base.observations),
        )
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else base.status,
            reason=None if measured else base.reason,
            observations=observations,
            evidence=base.evidence,
            provenance=base.provenance,
        )


def process_compliance_v5_scorer() -> ProcessComplianceV5Scorer:
    """Registry factory for ``process-compliance@5``."""
    return ProcessComplianceV5Scorer()


__all__ = [
    "PROCESS_COMPLIANCE_V5",
    "ProcessComplianceV5Config",
    "ProcessComplianceV5Scorer",
    "process_compliance_v5_scorer",
]
