"""``process-compliance``: deterministic trace checks only (plan G5; F7 change 2026-10-04).

For each reached checkpoint the step's trajectory is classified into process
events and two checks run over them:

- ``tests_run_before_finish``: tests ran after the last source edit;
- ``plan_written``: a plan tool or plan file came before the first edit.

A test command counts only when the trace shows it ran (a runner reported as not
found is dropped and counted in ``test_commands_not_run``). A decided check is
``ok`` (1 pass, 0 fail) with evidence at the deciding tool call. A step with no
source edit is ``not_applicable``. A check the trace cannot decide is
**undetermined**: status ``insufficient_evidence`` with an ``undetermined: <why>``
reason and no value, so it is never a zero and never enters ``compliance_rate``.
No model is asked: model judgement is used only when a needed measure cannot be
made deterministically, and none qualifies (requirements change log, 2026-10-04).
This package imports nothing from ``raidar.scoring.judge`` (an import-linter contract
enforces it).

Besides the step checks it reports the stall outcomes
(``continue-on-stall.json`` records), the work session's own test runs (the session
that received the step's first user prompt, not a wrapper's fix sessions) read three
ways, and the trajectory rates of each.

``compliance_rate`` is passed / decided over the eligible reached checkpoints; it
counts ``tests_run_before_finish`` only, unless the assessment says the condition
asks for a plan (scorer config ``plan_in_compliance_rate: true``). It is
``unavailable`` when a reached step has no readable trajectory.
"""

from __future__ import annotations

from pydantic import ValidationError

from raidar.scoring.framework.context import AssessContext, statused_result
from raidar.scoring.framework.manifest import MetricLevel, ScorerManifest
from raidar.scoring.framework.result import AssessResult, AssessStatus, Provenance
from raidar.scoring.process_checks.core import ProcessComplianceConfig, combine
from raidar.scoring.process_checks.manifest import PROCESS_COMPLIANCE
from raidar.scoring.process_checks.outputs import not_run_total
from raidar.scoring.process_checks.stall import STALL_METRICS, stall_observations
from raidar.scoring.process_checks.step import assess_step
from raidar.scoring.process_checks.work_tests import trajectory_rates

_OK = AssessStatus.OK
_CHECKPOINT_METRICS = tuple(
    metric.id
    for metric in PROCESS_COMPLIANCE.metrics
    if metric.level is MetricLevel.CHECKPOINT and metric not in STALL_METRICS
)


class DeterministicProcessComplianceScorer:
    """``process-compliance``: decides only what the trace decides."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE

    def assess(self, ctx: AssessContext) -> AssessResult:
        try:
            config = ProcessComplianceConfig.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                PROCESS_COMPLIANCE, ctx, AssessStatus.ERROR, f"invalid config: {exc}"
            )
        steps = [
            (inputs, assess_step(ctx, inputs, config, _CHECKPOINT_METRICS)) for inputs in ctx.steps
        ]
        base = combine(steps, Provenance())
        observations = (
            *base.observations,
            *stall_observations(ctx),
            not_run_total(base.observations),
            *trajectory_rates(base.observations),
        )
        measured = any(item.status is _OK for item in observations)
        return AssessResult(
            status=_OK if measured else base.status,
            reason=None if measured else base.reason,
            observations=observations,
            evidence=base.evidence,
            provenance=base.provenance,
        )


def deterministic_process_compliance_scorer() -> DeterministicProcessComplianceScorer:
    """Registry factory for ``process-compliance``."""
    return DeterministicProcessComplianceScorer()
