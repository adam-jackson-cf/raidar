"""The manipulation checks and the scorer refs that report each (G5).

A pre-registered check (``analysis.manipulation_checks``) names a trace check of one scorer:
``tests_run_before_finish`` and ``plan_written`` come from ``process-compliance``,
``gate_clean_at_step_end`` from ``gate-effort``. Experiment validation reads this table to refuse
an unknown check or a check whose scorer the experiment does not declare; analysis reads it to
find each check's results (``raidar.evaluation.analysis.manipulation``).
"""

from __future__ import annotations

from raidar.scoring.catalog.outcomes import GATE_EFFORT_REF, PROCESS_COMPLIANCE_REF
from raidar.scoring.process_checks.checks import PLAN_WRITTEN, TESTS_RUN_BEFORE_FINISH

DEFAULT_CHECKS = (TESTS_RUN_BEFORE_FINISH, PLAN_WRITTEN)
PROCESS_COMPLIANCE_REFS = (PROCESS_COMPLIANCE_REF,)
GATE_EFFORT_REFS = (GATE_EFFORT_REF,)
GATE_CLEAN = "gate_clean_at_step_end"
PROCESS_SOURCE = "process-compliance"
GATE_SOURCE = "gate-effort"
CHECK_SCORERS: dict[str, tuple[str, tuple[str, ...]]] = {
    TESTS_RUN_BEFORE_FINISH: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    PLAN_WRITTEN: (PROCESS_SOURCE, PROCESS_COMPLIANCE_REFS),
    GATE_CLEAN: (GATE_SOURCE, GATE_EFFORT_REFS),
}
"""``{check: (result source, scorer refs that report it)}``."""
