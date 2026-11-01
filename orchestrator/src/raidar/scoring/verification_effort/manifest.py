"""Manifest of ``verification-effort``: what the ``gate/1`` checks cost the agent, per step."""

from __future__ import annotations

from raidar.scoring.framework.manifest import EvidenceInput, ScorerManifest
from raidar.scoring.verification_effort.metrics import STEP_METRICS, TRAJECTORY_METRICS

VERIFICATION_EFFORT = ScorerManifest(
    id="verification-effort",
    version="1",
    description=(
        "The effort a package workflow's gate/1 checks cost per step, read from the host-written "
        "gate/1 record of each step's workflow record: seals, fix turns, first-pass and "
        "step-end cleanliness, blocking checks that failed, the fix turns' tokens, cost and wall "
        "time, and the fix turns' correctness (the hidden tests' case pass rate of the workspace "
        "the first fix turn started from, graded offline, against the step's final grade). A step "
        "that changed no production or test file is not applicable to first-pass cleanliness. "
        "Not applicable to a trajectory without a gate/1 package."
    ),
    runtimes=("*",),
    evidence_required=(
        EvidenceInput.TRACE,
        EvidenceInput.ORACLE_CASES,
        EvidenceInput.SNAPSHOT_CURRENT,
        EvidenceInput.SNAPSHOT_PRIOR,
    ),
    metrics=(*STEP_METRICS, *TRAJECTORY_METRICS),
)

__all__ = ["VERIFICATION_EFFORT"]
