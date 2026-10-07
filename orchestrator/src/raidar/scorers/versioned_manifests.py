"""Manifests that live beside their implementations, not in ``builtin_manifests``.

``code-quality@2``, ``process-compliance@2`` to ``@6`` and ``gate-effort@1`` to ``@8`` keep
their manifests next to their code so the released scorers' digests (which cover
``builtin_manifests``) stay unchanged; the catalog registers them from here.
"""

from __future__ import annotations

from raidar.scorers.code_quality_v2.scorer import CODE_QUALITY_V2
from raidar.scorers.gate_effort_manifests import GATE_EFFORT_MANIFESTS
from raidar.scorers.process_manifests import PROCESS_COMPLIANCE_MANIFESTS

VERSIONED_MANIFESTS = (
    *PROCESS_COMPLIANCE_MANIFESTS,
    *GATE_EFFORT_MANIFESTS,
    CODE_QUALITY_V2,
)
