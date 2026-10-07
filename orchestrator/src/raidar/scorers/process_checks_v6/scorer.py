"""``process-compliance@6``: process-compliance@5 telling "never tested" from "tested, then edited".

``process-compliance@5`` (``raidar.scorers.process_checks_v5``) is released and
unchanged. Its process-behaviour signal, ``work_session_tests_run_rate``, counts
a step only when the work session ran the tests after its last edit, so in
``smoke-3b-3`` a work session that never ran a test (r1, every step) and one
that ran them and then made one more edit (r2 at cp4, cp7 and cp8) both read 0.
@6 keeps every @5 metric, value, status, reason and evidence item, and adds two
checks over the same work-session events (``work_tests``):
``work_session_tests_run_any`` and ``work_session_tests_after_last_code_edit``
(edits to test files after the last run do not fail it), each with its rate over
decided steps.

Everything stays deterministic: no model is asked.
"""

from __future__ import annotations

from pydantic import ValidationError

from raidar.scorers.process_checks.core import combine
from raidar.scorers.process_checks_v3.scorer import STALL_METRICS, stall_observations
from raidar.scorers.process_checks_v4.scorer import _total
from raidar.scorers.process_checks_v5.scorer import (
    PROCESS_COMPLIANCE_V5,
    ProcessComplianceV5Config,
)
from raidar.scorers.process_checks_v6.step import assess_step
from raidar.scorers.process_checks_v6.work_tests import WORK_TESTS_METRICS, trajectory_rates
from raidar.scorers.sdk.context import AssessContext, statused_result
from raidar.scorers.sdk.manifest import MetricLevel, ScorerManifest
from raidar.scorers.sdk.result import AssessResult, AssessStatus, Provenance

_OK = AssessStatus.OK
PROCESS_COMPLIANCE_V6 = PROCESS_COMPLIANCE_V5.model_copy(
    update={
        "version": "6",
        "description": (
            "Deterministic trace checks of agent process (process-compliance@5: stall "
            "outcomes, a test command counts only when it ran, the step's work session's own "
            "test run after its last edit, a compliance rate without the plan check unless the "
            "condition asks for a plan); a check the trace cannot decide is undetermined, never "
            "judged by a model. Adds whether the work session ran the tests at all, and whether "
            "it ran them after its last edit to a non-test file."
        ),
        "metrics": (*PROCESS_COMPLIANCE_V5.metrics, *WORK_TESTS_METRICS),
    }
)
_CHECKPOINT_METRICS = tuple(
    metric.id
    for metric in PROCESS_COMPLIANCE_V6.metrics
    if metric.level is MetricLevel.CHECKPOINT and metric not in STALL_METRICS
)


class ProcessComplianceV6Scorer:
    """process-compliance@5 with the work session's tests read two more ways."""

    @property
    def manifest(self) -> ScorerManifest:
        return PROCESS_COMPLIANCE_V6

    def assess(self, ctx: AssessContext) -> AssessResult:
        try:
            config = ProcessComplianceV5Config.model_validate(dict(ctx.config))
        except ValidationError as exc:
            return statused_result(
                PROCESS_COMPLIANCE_V6, ctx, AssessStatus.ERROR, f"invalid config: {exc}"
            )
        steps = [
            (inputs, assess_step(ctx, inputs, config, _CHECKPOINT_METRICS)) for inputs in ctx.steps
        ]
        base = combine(steps, Provenance())
        observations = (
            *base.observations,
            *stall_observations(ctx),
            _total(base.observations),
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


def process_compliance_v6_scorer() -> ProcessComplianceV6Scorer:
    """Registry factory for ``process-compliance@6``."""
    return ProcessComplianceV6Scorer()


__all__ = [
    "PROCESS_COMPLIANCE_V6",
    "ProcessComplianceV6Scorer",
    "process_compliance_v6_scorer",
]
