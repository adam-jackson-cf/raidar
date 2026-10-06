"""Manifests that live beside their implementations, not in ``builtin_manifests``.

``process-compliance@2``/``@3`` and ``gate-effort@1`` to ``@5`` keep their manifests next to
their code so the released scorers' digests (which cover ``builtin_manifests``)
stay unchanged; the catalog registers them from here.
"""

from __future__ import annotations

from raidar.scorers.gate_effort.manifest import GATE_EFFORT
from raidar.scorers.gate_effort_v2.scorer import GATE_EFFORT_V2
from raidar.scorers.gate_effort_v3.scorer import GATE_EFFORT_V3
from raidar.scorers.gate_effort_v4.scorer import GATE_EFFORT_V4
from raidar.scorers.gate_effort_v5.scorer import GATE_EFFORT_V5
from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks_v3.scorer import PROCESS_COMPLIANCE_V3

VERSIONED_MANIFESTS = (
    PROCESS_COMPLIANCE_V2,
    PROCESS_COMPLIANCE_V3,
    GATE_EFFORT,
    GATE_EFFORT_V2,
    GATE_EFFORT_V3,
    GATE_EFFORT_V4,
    GATE_EFFORT_V5,
)
