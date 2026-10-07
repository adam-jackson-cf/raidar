"""Every ``gate-effort`` manifest, ``@1`` to ``@8``, from beside its implementation.

Collected here so the catalog's registration and the analysis read them through one
module (the released scorers' digests do not cover this file).
"""

from __future__ import annotations

from raidar.scorers.gate_effort.manifest import GATE_EFFORT
from raidar.scorers.gate_effort_v2.scorer import GATE_EFFORT_V2
from raidar.scorers.gate_effort_v3.scorer import GATE_EFFORT_V3
from raidar.scorers.gate_effort_v4.scorer import GATE_EFFORT_V4
from raidar.scorers.gate_effort_v5.scorer import GATE_EFFORT_V5
from raidar.scorers.gate_effort_v6.scorer import GATE_EFFORT_V6
from raidar.scorers.gate_effort_v7.scorer import GATE_EFFORT_V7
from raidar.scorers.gate_effort_v8.scorer import GATE_EFFORT_V8

GATE_EFFORT_MANIFESTS = (
    GATE_EFFORT,
    GATE_EFFORT_V2,
    GATE_EFFORT_V3,
    GATE_EFFORT_V4,
    GATE_EFFORT_V5,
    GATE_EFFORT_V6,
    GATE_EFFORT_V7,
    GATE_EFFORT_V8,
)

__all__ = [
    "GATE_EFFORT",
    "GATE_EFFORT_MANIFESTS",
    "GATE_EFFORT_V2",
    "GATE_EFFORT_V3",
    "GATE_EFFORT_V4",
    "GATE_EFFORT_V5",
    "GATE_EFFORT_V6",
    "GATE_EFFORT_V7",
    "GATE_EFFORT_V8",
]
