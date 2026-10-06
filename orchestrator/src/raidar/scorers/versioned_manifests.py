"""Manifests that live beside their implementations, not in ``builtin_manifests``.

``code-quality@2``, ``process-compliance@2`` to ``@4`` and ``gate-effort@1`` to ``@6`` keep
their manifests next to their code so the released scorers' digests (which cover
``builtin_manifests``) stay unchanged; the catalog registers them from here.
"""

from __future__ import annotations

from raidar.scorers.code_quality_v2.scorer import CODE_QUALITY_V2
from raidar.scorers.gate_effort.manifest import GATE_EFFORT
from raidar.scorers.gate_effort_v2.scorer import GATE_EFFORT_V2
from raidar.scorers.gate_effort_v3.scorer import GATE_EFFORT_V3
from raidar.scorers.gate_effort_v4.scorer import GATE_EFFORT_V4
from raidar.scorers.gate_effort_v5.scorer import GATE_EFFORT_V5
from raidar.scorers.gate_effort_v6.scorer import GATE_EFFORT_V6
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks_v3.scorer import PROCESS_COMPLIANCE_V3
from raidar.scorers.process_checks_v4.scorer import PROCESS_COMPLIANCE_V4

VERSIONED_MANIFESTS = (
    PROCESS_COMPLIANCE_V2,
    PROCESS_COMPLIANCE_V3,
    PROCESS_COMPLIANCE_V4,
    GATE_EFFORT,
    GATE_EFFORT_V2,
    GATE_EFFORT_V3,
    GATE_EFFORT_V4,
    GATE_EFFORT_V5,
    GATE_EFFORT_V6,
    CODE_QUALITY_V2,
)
