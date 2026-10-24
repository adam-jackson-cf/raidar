"""Manifest of ``process-compliance``: the metrics the deterministic process checks report.

The trace checks (``tests_run_before_finish``, ``plan_written`` and the compliance
rate) are the deterministic metrics of the built-in ``process-compliance`` manifest.
A check the trace cannot decide is ``insufficient_evidence`` with an
``undetermined: ...`` reason, never a value, so it never reads as a failure. Added
here: the stall outcomes, the count of test commands that never ran, and the work
session's own test runs.
"""

from __future__ import annotations

from raidar.scorers.builtin_manifests import PROCESS_COMPLIANCE as _TRACE_CHECKS
from raidar.scorers.process_checks.outputs import NOT_RUN_METRICS
from raidar.scorers.process_checks.stall import STALL_METRICS
from raidar.scorers.process_checks.work_session import WORK_SESSION_METRICS
from raidar.scorers.process_checks.work_tests import WORK_TESTS_METRICS
from raidar.scorers.sdk.manifest import EvidenceInput, ScorerManifest, Tier

PROCESS_COMPLIANCE = ScorerManifest(
    id="process-compliance",
    version="6",
    description=(
        "Deterministic trace checks of agent process (stall "
        "outcomes, a test command counts only when it ran, the step's work session's own "
        "test run after its last edit, a compliance rate without the plan check unless the "
        "condition asks for a plan); a check the trace cannot decide is undetermined, never "
        "judged by a model. Adds whether the work session ran the tests at all, and whether "
        "it ran them after its last edit to a non-test file."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE, EvidenceInput.SNAPSHOT_CURRENT),
    metrics=(
        *(metric for metric in _TRACE_CHECKS.metrics if metric.tier is Tier.DETERMINISTIC),
        *STALL_METRICS,
        *NOT_RUN_METRICS,
        *WORK_SESSION_METRICS,
        *WORK_TESTS_METRICS,
    ),
)
