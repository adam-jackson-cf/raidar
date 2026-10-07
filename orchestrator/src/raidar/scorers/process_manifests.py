"""Every deterministic ``process-compliance`` manifest, ``@2`` to ``@6``, from beside its code.

Collected here so the catalog's registration and the analysis read them through one
module (the released scorers' digests do not cover this file).
"""

from __future__ import annotations

from raidar.scorers.process_checks.manifest import PROCESS_COMPLIANCE_V2
from raidar.scorers.process_checks_v3.scorer import PROCESS_COMPLIANCE_V3
from raidar.scorers.process_checks_v4.scorer import PROCESS_COMPLIANCE_V4
from raidar.scorers.process_checks_v5.scorer import PROCESS_COMPLIANCE_V5
from raidar.scorers.process_checks_v6.scorer import PROCESS_COMPLIANCE_V6

PROCESS_COMPLIANCE_MANIFESTS = (
    PROCESS_COMPLIANCE_V2,
    PROCESS_COMPLIANCE_V3,
    PROCESS_COMPLIANCE_V4,
    PROCESS_COMPLIANCE_V5,
    PROCESS_COMPLIANCE_V6,
)

__all__ = [
    "PROCESS_COMPLIANCE_MANIFESTS",
    "PROCESS_COMPLIANCE_V2",
    "PROCESS_COMPLIANCE_V3",
    "PROCESS_COMPLIANCE_V4",
    "PROCESS_COMPLIANCE_V5",
    "PROCESS_COMPLIANCE_V6",
]
