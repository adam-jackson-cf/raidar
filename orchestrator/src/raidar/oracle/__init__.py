"""Raidar oracle plugin: host-side hidden-test verification (reboot plan WS-E).

Entry points for other workstreams:

- ``raidar.oracle.verifier:RaidarOracleVerifier`` - Harbor ``verifier.import_path``.
- ``raidar.oracle.guard.install_leak_guard`` - required pre-agent leak check (trial hook).
- ``raidar.oracle.bundle`` - host-only bundle format, ``build_bundle_from_scb_harbor_task``.
- ``raidar.oracle.snapshot.app_artifact_config`` - the ``/app`` step artifact entry.
- ``raidar.oracle.grader.grade_step`` - grade one snapshot (used by regrade).
- ``raidar.oracle.records`` - host-only per-step records and G-kernel evidence.
- ``raidar.oracle.regrade.regrade_path`` - ``raidar assess --regrade``.
"""

from raidar.oracle.models import CaseGroup, CaseResult, CaseStatus, OracleGrade, ParityVerdict

__all__ = ["CaseGroup", "CaseResult", "CaseStatus", "OracleGrade", "ParityVerdict"]
