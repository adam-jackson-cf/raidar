"""Raidar verifier: host-side hidden-test verification (reboot plan WS-E).

Entry points for other workstreams:

- ``raidar.verifier.verifier:RaidarVerifier`` - Harbor ``verifier.import_path``.
- ``raidar.verifier.guard.install_leak_guard`` - required pre-agent leak check (trial hook).
- ``raidar.verifier.bundle`` - host-only bundle format, ``build_bundle_from_scb_harbor_task``.
- ``raidar.core.snapshot.app_artifact_config`` - the ``/app`` step artifact entry.
- ``raidar.verifier.grader.grade_step`` - grade one snapshot (used by regrade).
- ``raidar.verifier.records`` - host-only per-step records and G-kernel evidence.
- ``raidar.verifier.regrade.regrade_path`` - ``raidar assess --regrade``.
"""

from raidar.verifier.models import CaseGroup, CaseResult, CaseStatus, ParityVerdict, VerifierGrade

__all__ = ["CaseGroup", "CaseResult", "CaseStatus", "VerifierGrade", "ParityVerdict"]
