"""Case-level coverage parity for G: leave out the cases whose outcome flipped, not the step.

The SCB fork reads a step's G as
``ineligible`` (``coverage_parity_failed``) whenever the coverage run's outcomes
differ from the grading run's in any case. One timing-sensitive grader case
that passes in grading and fails under coverage therefore removes the step's
G, and through ``problem_regression`` the trial's headline score
(smoke-3b-4, rationale 17).

``reconcile`` narrows that rule. When the only parity failures are outcome
flips (reasons within ``CASE_LEVEL_REASONS``) and both runs are otherwise the
same run (environment, corpus, invocation, process ledger and the set of
collected cases all equal, each run's outcome list equal to its ledger), the
flipped cases, at most ``MAX_EXCLUDED_CASES``, are left out of both runs'
outcomes, ledgers, coverage contexts and process events, and G is computed on
the rest by the unchanged kernel. Anything else stays ``ineligible``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raidar.grading.coverage_store import read_offline_coverage
from raidar.grading.models import ParityVerdict
from raidar.grading.records import FORK_EVIDENCE_FILE
from raidar.scoring.code_quality.lineage_models import RegressionRunEvidence
from raidar.scoring.code_quality.transition import verifier_record_dir
from raidar.scoring.framework.context import StepInputs

# Parity reasons that a case-level exclusion can settle: only outcomes differ.
CASE_LEVEL_REASONS = frozenset({"ledger_mismatch", "outcome_mismatch"})
# At most this many flipped cases are left out; more than that is not a flaky case.
MAX_EXCLUDED_CASES = 3
CASE_EXCLUDED = "coverage_parity_cases_excluded"

_Row = tuple[str, ...]
ParitySource = tuple[ParityVerdict | None, dict[str, Any] | None]


def _rows(run: RegressionRunEvidence) -> dict[str, set[_Row]]:
    """Each case's ledger and outcome rows (phase, outcome; group for outcomes)."""
    rows: dict[str, set[_Row]] = defaultdict(set)
    for entry in run.ledger:
        rows[entry.node_id].add(("ledger", entry.phase.value, entry.outcome.value))
    for outcome in run.outcomes:
        rows[outcome.node_id].add(
            ("outcome", outcome.group_type, outcome.phase.value, outcome.outcome.value)
        )
    return rows


def _consistent(run: RegressionRunEvidence) -> bool:
    outcomes = [(item.node_id, item.phase, item.outcome) for item in run.outcomes]
    return outcomes == [(item.node_id, item.phase, item.outcome) for item in run.ledger]


def flipped_cases(
    baseline: RegressionRunEvidence, produced: RegressionRunEvidence
) -> tuple[str, ...] | None:
    """The cases whose outcome differs between the runs, or None when more than that differs."""
    if (
        baseline.environment != produced.environment
        or baseline.corpus != produced.corpus
        or baseline.invocation != produced.invocation
        or baseline.process_events != produced.process_events
        or not (_consistent(baseline) and _consistent(produced))
    ):
        return None
    left, right = _rows(baseline), _rows(produced)
    if left.keys() != right.keys():
        return None
    return tuple(sorted(node for node in left if left[node] != right[node]))


def _without(run: RegressionRunEvidence, nodes: Iterable[str]) -> RegressionRunEvidence:
    drop = frozenset(nodes)
    return run.model_copy(
        update={
            "outcomes": tuple(item for item in run.outcomes if item.node_id not in drop),
            "ledger": tuple(item for item in run.ledger if item.node_id not in drop),
            "coverage": tuple(item for item in run.coverage if item.node_id not in drop),
            "process_events": tuple(
                item for item in run.process_events if item.node_id not in drop
            ),
        }
    )


def fork_runs(
    fork: dict[str, Any],
) -> tuple[RegressionRunEvidence, RegressionRunEvidence] | None:
    """The G input's two runs (baseline, produced), or None when it does not parse."""
    try:
        return (
            RegressionRunEvidence.model_validate(fork["baseline"]),
            RegressionRunEvidence.model_validate(fork["produced"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


def reconcile(
    reasons: Iterable[str], baseline: RegressionRunEvidence, produced: RegressionRunEvidence
) -> tuple[RegressionRunEvidence, RegressionRunEvidence, tuple[str, ...]] | None:
    """Both runs without their flipped cases, and those cases; None when G stays ineligible."""
    found = frozenset(reasons)
    if not found or not found <= CASE_LEVEL_REASONS:
        return None
    flipped = flipped_cases(baseline, produced)
    if not flipped or len(flipped) > MAX_EXCLUDED_CASES:
        return None
    return _without(baseline, flipped), _without(produced, flipped), flipped


def _in_trial_fork(step: StepInputs) -> dict[str, Any] | None:
    directory = verifier_record_dir(step)
    path = directory / FORK_EVIDENCE_FILE if directory is not None else None
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def parity_source(step: StepInputs, store_root: Path | None) -> ParitySource | None:
    """The parity verdict and G input behind a step, or None when there is none to read."""
    record = step.record
    if record is None:
        return None
    if record.coverage.enabled:
        return record.coverage.parity, _in_trial_fork(step)
    if store_root is None:
        return None
    stored = read_offline_coverage(
        store_root, record.bundle.digest, record.step, record.snapshot.digest
    )
    return (stored.parity, stored.fork) if stored is not None else None


__all__ = [
    "CASE_EXCLUDED",
    "CASE_LEVEL_REASONS",
    "MAX_EXCLUDED_CASES",
    "ParitySource",
    "flipped_cases",
    "fork_runs",
    "parity_source",
    "reconcile",
]
