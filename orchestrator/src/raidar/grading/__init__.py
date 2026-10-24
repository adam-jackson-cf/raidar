"""Raidar verifier: host-side hidden-test verification (reboot plan WS-E).

Entry points for other workstreams:

- ``raidar.grading.verifier:RaidarVerifier`` - Harbor ``verifier.import_path``.
- ``raidar.grading.guard.install_leak_guard`` - required pre-agent leak check (trial hook).
- ``raidar.grading.bundle`` - host-only bundle format, ``build_bundle_from_scb_harbor_task``.
- ``raidar.contract.snapshot.app_artifact_config`` - the ``/app`` step artifact entry.
- ``raidar.grading.grader.grade_step`` - grade one snapshot (used by regrade).
- ``raidar.grading.records`` - host-only per-step records and G-kernel evidence.
- ``raidar.grading.regrade.regrade_path`` - ``raidar assess --regrade``.
"""

from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, ParityVerdict, VerifierGrade

__all__ = ["CaseGroup", "CaseResult", "CaseStatus", "VerifierGrade", "ParityVerdict"]
