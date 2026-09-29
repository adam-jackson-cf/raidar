"""Coverage parity between the grading run and the separate coverage run.

The grading run is never instrumented (oracle-validation.md §6: subprocess
coverage slowed a submission ~5x and flipped a timeout-bound verdict). Per-test
coverage for G therefore comes from a second run on a fresh copy of the same
snapshot. Its coverage is only attributable to the graded outcomes when the two
runs match exactly; otherwise G is ``ineligible`` with typed reasons, never 0.

The checks mirror the SCB fork's G kernel precondition
(``metrics/scoring/regression.py::_parity_available``): identical environment,
corpus and invocation; identical, internally consistent outcome ledgers and
process ledgers; coverage only for nodes that have outcomes. The coverage
wrapper (``measurement_prefix``) is excluded from the invocation identity by
design and recorded separately.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from raidar.oracle.models import (
    CaseGroup,
    CaseResult,
    ParityVerdict,
    RunEvidence,
)


def ineligible(*reasons: str) -> ParityVerdict:
    return ParityVerdict(eligible=False, reasons=tuple(sorted(set(reasons))))


def _case_view(cases: Sequence[CaseResult]) -> dict[str, tuple[str, str, str]]:
    return {c.id: (c.group.value, c.checkpoint_origin, c.status.value) for c in cases}


def _mismatch_reasons(grading: RunEvidence, coverage: RunEvidence) -> list[str]:
    reasons: list[str] = []
    if grading.environment != coverage.environment:
        reasons.append("environment_mismatch")
    if grading.corpus != coverage.corpus:
        reasons.append("corpus_mismatch")
    if grading.invocation != coverage.invocation:
        reasons.append("invocation_mismatch")
    if grading.ledger != coverage.ledger:
        reasons.append("ledger_mismatch")
    if grading.process_events != coverage.process_events:
        reasons.append("process_ledger_mismatch")
    return reasons


def check_parity(
    grading: RunEvidence,
    coverage: RunEvidence,
    grading_cases: Sequence[CaseResult],
    coverage_cases: Sequence[CaseResult],
) -> ParityVerdict:
    reasons = _mismatch_reasons(grading, coverage)
    if _case_view(grading_cases) != _case_view(coverage_cases):
        reasons.append("outcome_mismatch")
    ledger_nodes = {entry.node_id for entry in coverage.ledger}
    if any(context.node_id not in ledger_nodes for context in coverage.coverage):
        reasons.append("coverage_outside_outcomes")
    if coverage.invalid_coverage_paths:
        reasons.append("invalid_coverage_paths")
    return ineligible(*reasons) if reasons else ParityVerdict(eligible=True)


def outcome_differences(
    grading_cases: Sequence[CaseResult], coverage_cases: Sequence[CaseResult], limit: int = 20
) -> list[str]:
    """Human-readable per-case differences, for the step record."""
    left, right = _case_view(grading_cases), _case_view(coverage_cases)
    notes = [f"{k}: only in grading" for k in sorted(left.keys() - right.keys())]
    notes += [f"{k}: only in coverage" for k in sorted(right.keys() - left.keys())]
    notes += [
        f"{k}: grading={left[k][2]} coverage={right[k][2]}"
        for k in sorted(left.keys() & right.keys())
        if left[k] != right[k]
    ]
    return notes[:limit]


# ------------------------------------------------------- fork (G2) handoff
_FORK_GROUP = {
    CaseGroup.CORE: "Core",
    CaseGroup.FUNCTIONALITY: "Functionality",
    CaseGroup.ERROR: "Error",
    CaseGroup.REGRESSION: "Regression",
}


def _hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fork_regression_run_evidence(run: RunEvidence, cases: Sequence[CaseResult]) -> dict[str, Any]:
    """Project a run onto the SCB fork's ``RegressionRunEvidence`` JSON shape.

    Lets the ported G kernel (WS-G G2) consume oracle evidence unchanged.
    """
    group = {case.id: _FORK_GROUP[case.group] for case in cases}
    env = run.environment
    ledger = [entry.model_dump() for entry in run.ledger]
    return {
        "environment": {
            "environment_id": _hex(env.grader_environment_id),
            "project_sha256": _hex(f"{env.grader_environment_id}:{env.grader_tools_sha256}"),
            "lock_sha256": env.grader_tools_sha256,
            "platform_sha256": env.platform_sha256,
            "plugin_sha256": env.plugin_sha256,
            "interpreter_sha256": _hex(
                env.grader_interpreter_sha256 + env.submission_interpreter_sha256
            ),
        },
        "corpus": {
            "corpus_id": run.corpus.corpus_sha256,
            "manifest_sha256": _hex(json.dumps(run.corpus.selected_tests)),
        },
        "invocation": {"argv": list(run.invocation.argv), "cwd": run.invocation.cwd},
        "outcomes": [
            {**entry, "group_type": group[entry["node_id"]]}
            for entry in ledger
            if entry["node_id"] in group
        ],
        "ledger": [entry for entry in ledger if entry["node_id"] in group],
        "coverage": [context.model_dump() for context in run.coverage],
        "process_events": [event.model_dump() for event in run.process_events],
    }
