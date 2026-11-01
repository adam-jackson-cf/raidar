"""The manipulation checks and the scorer refs that report each (G5).

A check is a trace check of one scorer: ``tests_run_before_finish`` and ``plan_written`` come
from ``process-compliance``, ``gate_clean_at_step_end`` from ``verification-effort``. Analysis reads
this table to find each check's results (``raidar.evaluation.analysis.manipulation``).
"""

from __future__ import annotations

from raidar.scoring.catalog.outcomes import PROCESS_COMPLIANCE_REF, VERIFICATION_EFFORT_REF
from raidar.scoring.process_checks.checks import PLAN_WRITTEN, TESTS_RUN_BEFORE_FINISH

DEFAULT_CHECKS = (TESTS_RUN_BEFORE_FINISH, PLAN_WRITTEN)
PROCESS_COMPLIANCE_REFS = (PROCESS_COMPLIANCE_REF,)
VERIFICATION_EFFORT_REFS = (VERIFICATION_EFFORT_REF,)
GATE_CLEAN = "gate_clean_at_step_end"
PROCESS_SOURCE = "process-compliance"
GATE_SOURCE = "verification-effort"
CHECK_SCORERS: dict[str, tuple[str, tuple[str, ...]]] = {
    TESTS_RUN_BEFORE_FINISH: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    PLAN_WRITTEN: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    GATE_CLEAN: (GATE_SOURCE, VERIFICATION_EFFORT_REFS),
}
"""``{check: (result source, scorer refs that report it)}``."""
