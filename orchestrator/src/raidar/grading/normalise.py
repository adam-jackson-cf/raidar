"""Normalise a sandbox ledger into ``CaseResult`` rows (SCB group semantics).

SCB rule (``slop_code/evaluation/report.py`` GroupType; the Harbor converter's
``_ctrf_to_reward.classify``):

- a test from a prior checkpoint's file is ``regression`` regardless of markers;
- otherwise ``@pytest.mark.error`` -> ``error``, ``regression`` -> ``regression``,
  ``functionality`` -> ``functionality``, unmarked -> ``core``.

``checkpoint_origin`` is the checkpoint named by the test file
(``test_<checkpoint>.py``), or the graded checkpoint when the file names none.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from raidar.grading.models import CaseGroup, CaseResult, CaseStatus, InfraFailure

CHECKPOINT_FILE = re.compile(r"test_(checkpoint_\d+)\.py")
PYTEST_TESTS_FAILED = 1
_OK_EXIT_STATUSES = frozenset({0, PYTEST_TESTS_FAILED})


def checkpoint_origin(node_id: str, current: str) -> str:
    match = CHECKPOINT_FILE.search(node_id.split("::", 1)[0])
    return match.group(1) if match else current


def classify(node_id: str, markers: Sequence[str], current: str) -> CaseGroup:
    if checkpoint_origin(node_id, current) != current:
        return CaseGroup.REGRESSION
    names = set(markers)
    if "error" in names:
        return CaseGroup.ERROR
    if "regression" in names:
        return CaseGroup.REGRESSION
    if "functionality" in names:
        return CaseGroup.FUNCTIONALITY
    return CaseGroup.CORE


def case_results(ledger: Mapping[str, Any], checkpoint: str) -> tuple[CaseResult, ...]:
    return tuple(
        CaseResult(
            id=str(node["node_id"]),
            group=classify(str(node["node_id"]), node.get("markers", []), checkpoint),
            checkpoint_origin=checkpoint_origin(str(node["node_id"]), checkpoint),
            status=CaseStatus(node["status"]),
        )
        for node in sorted(ledger.get("nodes", []), key=lambda n: str(n["node_id"]))
    )


def ledger_infra_failure(ledger: Mapping[str, Any]) -> InfraFailure | None:
    """Classify runs whose outcome cannot be trusted as a verdict."""
    if ledger.get("collect_errors"):
        return InfraFailure(code="collection_error", detail=", ".join(ledger["collect_errors"]))
    nodes = ledger.get("nodes", [])
    if not nodes:
        return InfraFailure(code="no_tests_collected")
    unrun = [str(node["node_id"]) for node in nodes if not node.get("ran")]
    if unrun:
        return InfraFailure(code="incomplete_run", detail=f"{len(unrun)} cases never ran")
    status = ledger.get("exit_status")
    if status not in _OK_EXIT_STATUSES:
        return InfraFailure(code="pytest_exit_status", detail=str(status))
    return None


_FAILING_STATUSES = frozenset({"failed", "error"})


def _exit_mismatch(ledger: Mapping[str, Any], exit_code: int | None) -> InfraFailure | None:
    status = ledger.get("exit_status")
    if exit_code is not None and status != exit_code:
        return InfraFailure(
            code="ledger_exit_mismatch", detail=f"sandbox exit {exit_code}, ledger {status}"
        )
    failing = any(str(node.get("status")) in _FAILING_STATUSES for node in ledger.get("nodes", []))
    if (status == 0 and failing) or (status == PYTEST_TESTS_FAILED and not failing):
        return InfraFailure(
            code="ledger_exit_mismatch", detail=f"exit status {status} contradicts node outcomes"
        )
    return None


def _corpus_mismatch(ledger: Mapping[str, Any], test_files: Sequence[str]) -> InfraFailure | None:
    node_files = {str(node["node_id"]).split("::", 1)[0] for node in ledger.get("nodes", [])}

    def covered(name: str) -> bool:
        return any(path == name or path.endswith(f"/{name}") for path in node_files)

    missing = sorted(name for name in test_files if not covered(name))
    if missing:
        return InfraFailure(
            code="ledger_corpus_mismatch", detail=f"no cases from {', '.join(missing)}"
        )
    return None


def ledger_consistency_failure(
    ledger: Mapping[str, Any], exit_code: int | None, test_files: Sequence[str]
) -> InfraFailure | None:
    """Refuse a ledger that disagrees with what the host observed.

    The submission runs as the same uid as pytest and can write files, so the
    ledger is checked against facts it cannot forge: the container's own exit
    code, the exit status the node outcomes imply, and the selected test files.
    """
    return _exit_mismatch(ledger, exit_code) or _corpus_mismatch(ledger, test_files)


def no_graded_cases(cases: Sequence[CaseResult]) -> InfraFailure | None:
    """Every case skipped: no verdict exists, which is unavailable, not a failure."""
    if cases and all(case.status is CaseStatus.SKIPPED for case in cases):
        return InfraFailure(code="no_graded_cases", detail=f"{len(cases)} cases all skipped")
    return None


def timing_sensitive(ledger: Mapping[str, Any], timeout_s: float | None) -> tuple[str, ...]:
    """Cases within 2x of their per-test timeout (oracle-validation.md §4), plus
    cases failed because the run's grading budget ran out (their verdict depends
    on how fast the earlier cases ran)."""
    near_timeout = (
        set()
        if timeout_s is None
        else {
            str(node["node_id"])
            for node in ledger.get("nodes", [])
            if float(node.get("duration_s", 0.0)) * 2 >= timeout_s
        }
    )
    return tuple(sorted(near_timeout | {str(n) for n in ledger.get("budget_exhausted", [])}))
