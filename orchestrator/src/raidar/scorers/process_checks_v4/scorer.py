"""``process-compliance@4``: process-compliance@3 counting only test commands that ran.

``process-compliance@3`` (``raidar.scorers.process_checks_v3``) is released and
unchanged. Its ``tests_run_before_finish`` matches command text only, so in
``smoke-04`` of the guardrails experiment a ``python -m pytest -q`` that failed
with "No module named pytest" counted as a test run. @4 keeps every @3 metric,
value, status and reason except that check, which now counts a test command
only when the trace shows it ran:

- a test command (a ``test`` event of the classifier: the default runners
  plus the assessment's ``extra_test_patterns``) **did not run** when the
  output its tool call recorded says its runner was not found:
  ``<runner>: command not found`` or ``<runner>: not found`` (shells),
  ``<runner>: No such file or directory``, ``Failed to spawn: `<runner>``` (uv)
  or ``No module named <module>`` for ``python -m <module>``. The runner is the
  command's first word after launcher prefixes (``uv run``, ``timeout 60``,
  ``FOO=1`` ...), or the module of ``python -m``;
- a command whose call recorded no output keeps @3's reading (it counts):
  the trace cannot show that it did not run;
- such commands are dropped before the checks run, so a step whose only test
  command never ran fails ``tests_run_before_finish``.

The commit gate a pack installs runs the project's tests too; an experiment
counts it as a test run by naming it in ``extra_test_patterns`` (for
python-guardrails, ``guardrail-check``), so nothing here names a pack.

Added, descriptive: ``test_commands_not_run`` (checkpoint: test commands in the
step whose runner was not found) and ``total_test_commands_not_run``
(trajectory). Everything stays deterministic: no model is asked.
"""

from __future__ import annotations

from collections.abc import Sequence

from raidar.scorers.process_checks.checks import run_checks
from raidar.scorers.process_checks.core import (
    ProcessComplianceConfig,
    StepAssessment,
    check_observation,
    decided_tally,
    measurable,
    step_evidence,
)
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks.scorer import UNDETERMINED
from raidar.scorers.process_checks.trace_events import classify
from raidar.scorers.process_checks_v3.scorer import (
    PROCESS_COMPLIANCE_V3,
    ProcessComplianceV3Scorer,
)
from raidar.scorers.process_checks_v4.outputs import (
    call_outputs,
    not_found,
    runner_of,
    split_unrun,
)
from raidar.scorers.sdk.context import AssessContext, StepInputs
from raidar.scorers.sdk.manifest import (
    Direction,
    Exactness,
    MetricLevel,
    MetricSpec,
    MetricType,
    ScorerManifest,
    Tier,
)
from raidar.scorers.sdk.result import (
    TRAJECTORY_SUBJECT,
    AssessResult,
    AssessStatus,
    Observation,
)

NOT_RUN = "test_commands_not_run"
TOTAL_NOT_RUN = f"total_{NOT_RUN}"

NOT_RUN_METRICS = (
    MetricSpec(
        id=NOT_RUN,
        description="Test commands in the step whose runner was not found (they never ran).",
        level=MetricLevel.CHECKPOINT,
        type=MetricType.COUNT,
        direction=Direction.LOWER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
    MetricSpec(
        id=TOTAL_NOT_RUN,
        description="Test commands whose runner was not found, over the trajectory.",
        level=MetricLevel.TRAJECTORY,
        type=MetricType.COUNT,
        direction=Direction.LOWER_BETTER,
        tier=Tier.DETERMINISTIC,
        exactness=Exactness.NATIVE,
    ),
)
PROCESS_COMPLIANCE_V4 = ScorerManifest(
    id="process-compliance",
    version="4",
    description=(
        "Deterministic trace checks of agent process (process-compliance@3, stall outcomes "
        "included); a check the trace cannot decide is undetermined, never judged by a "
        "model. A test command counts as a test run only when its recorded output does not "
        "say its runner was not found; such commands are counted separately."
    ),
    runtimes=PROCESS_COMPLIANCE_V3.runtimes,
    evidence_required=PROCESS_COMPLIANCE_V3.evidence_required,
    metrics=(*PROCESS_COMPLIANCE_V3.metrics, *NOT_RUN_METRICS),
)
# @2's checkpoint checks and the not-run count (@3's stall metrics are added by @3's assess).
_CHECKPOINT_METRICS = (
    *(m.id for m in PROCESS_COMPLIANCE_V2.metrics if m.level is MetricLevel.CHECKPOINT),
    NOT_RUN,
)


# ---- the scorer -------------------------------------------------------------------------


class ProcessComplianceV4Scorer(ProcessComplianceV3Scorer):
    """process-compliance@3 whose test-run check counts only test commands that ran."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE_V4

    def _step(
        self, ctx: AssessContext, inputs: StepInputs, config: ProcessComplianceConfig
    ) -> StepAssessment:
        trajectory = measurable(ctx, inputs, _CHECKPOINT_METRICS)
        if isinstance(trajectory, StepAssessment):
            return trajectory
        events = classify(trajectory, config.classifier())
        split = split_unrun(events, call_outputs(trajectory.path))
        outcomes = run_checks(split.kept)
        passed, decided = decided_tally(outcomes)
        not_run = Observation(
            metric=NOT_RUN, subject=inputs.step, status=AssessStatus.OK, value=len(split.not_run)
        )
        return StepAssessment(
            observations=(
                *(check_observation(inputs.step, outcome, UNDETERMINED) for outcome in outcomes),
                not_run,
            ),
            evidence=step_evidence(inputs, outcomes, trajectory),
            passed=passed,
            decided=decided,
        )

    def assess(self, ctx: AssessContext) -> AssessResult:
        result = super().assess(ctx)
        if result.status is AssessStatus.ERROR:
            gap = Observation(
                metric=TOTAL_NOT_RUN,
                subject=TRAJECTORY_SUBJECT,
                status=AssessStatus.ERROR,
                reason=result.reason or "error",
            )
            steps = tuple(
                Observation(metric=NOT_RUN, subject=s, status=AssessStatus.ERROR, reason=gap.reason)
                for s in ctx.subjects(MetricLevel.CHECKPOINT)
            )
            return result.model_copy(update={"observations": (*result.observations, *steps, gap)})
        return result.model_copy(
            update={"observations": (*result.observations, _total(result.observations))}
        )


def _total(observations: Sequence[Observation]) -> Observation:
    per_step = [item for item in observations if item.metric == NOT_RUN]
    gaps = [item.subject for item in per_step if item.status is AssessStatus.UNAVAILABLE]
    if gaps:
        return Observation(
            metric=TOTAL_NOT_RUN,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.UNAVAILABLE,
            reason=f"{NOT_RUN} is unavailable at {', '.join(gaps)}",
        )
    counted = [item.value or 0.0 for item in per_step if item.status is AssessStatus.OK]
    if not counted:
        return Observation(
            metric=TOTAL_NOT_RUN,
            subject=TRAJECTORY_SUBJECT,
            status=AssessStatus.NOT_APPLICABLE,
            reason="no reached step with a readable trajectory",
        )
    return Observation(
        metric=TOTAL_NOT_RUN, subject=TRAJECTORY_SUBJECT, status=AssessStatus.OK, value=sum(counted)
    )


def process_compliance_v4_scorer() -> ProcessComplianceV4Scorer:
    """Registry factory for ``process-compliance@4``."""
    return ProcessComplianceV4Scorer()


__all__ = [
    "PROCESS_COMPLIANCE_V4",
    "ProcessComplianceV4Scorer",
    "call_outputs",
    "not_found",
    "process_compliance_v4_scorer",
    "runner_of",
    "split_unrun",
]
