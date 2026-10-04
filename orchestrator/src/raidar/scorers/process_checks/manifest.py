"""Manifest of ``process-compliance@2`` (Jev parked, 2026-10-04).

The same deterministic checks as ``@1`` with no Jev metric and no Jev runtime.
A check no rule decides is ``insufficient_evidence`` with an
``undetermined: ...`` reason, never a value, so it never reads as a failure.

It lives here rather than in ``builtin_manifests`` because every released
scorer's implementation digest covers that module: adding ``@2`` there would
change the identity of ``correctness@1``, ``code-quality@1`` and
``resource-usage@1`` and orphan their stored Assessments.
"""

from __future__ import annotations

from raidar.scorers.builtin_manifests import PROCESS_COMPLIANCE
from raidar.scorers.sdk.manifest import EvidenceInput, ScorerManifest, Tier

PROCESS_COMPLIANCE_V2 = ScorerManifest(
    id="process-compliance",
    version="2",
    description=(
        "Deterministic trace checks of agent process; a check the trace cannot decide "
        "is undetermined, never judged by a model."
    ),
    runtimes=("*",),
    evidence_required=(EvidenceInput.TRACE, EvidenceInput.SNAPSHOT_CURRENT),
    metrics=tuple(
        metric for metric in PROCESS_COMPLIANCE.metrics if metric.tier is Tier.DETERMINISTIC
    ),
)
