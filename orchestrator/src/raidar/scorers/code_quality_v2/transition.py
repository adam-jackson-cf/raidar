"""``code-quality@2``'s R and G per transition: @1's, with G's run evidence from a reader.

A step whose trial made the verifier's coverage pass is read exactly as
``code-quality@1`` reads it (``raidar.scorers.code_quality.transition``); a step
whose trial deferred it reads the offline run from the Assessment store
(``raidar.verifier.coverage_store``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from raidar.scorers.code_quality.checkpoint import CheckpointMeasure, Gap, Measured
from raidar.scorers.code_quality.diffs import changed_lines, regression_scope
from raidar.scorers.code_quality.lineage import Ledger, next_ledger
from raidar.scorers.code_quality.lineage_models import (
    RegressionAttribution,
    RegressionBreadthInput,
    RegressionRunEvidence,
    TransitionReworkEvidence,
)
from raidar.scorers.code_quality.regression import PARITY_UNAVAILABLE, calculate_regression_breadth
from raidar.scorers.code_quality.transition import (
    PARITY_FAILED,
    TransitionMeasure,
    _pair,
    _rework,
    _run_evidence,
)
from raidar.scorers.sdk.context import StepInputs
from raidar.scorers.sdk.result import AssessStatus
from raidar.verifier.coverage_store import read_offline_coverage

OFFLINE_NOT_PRODUCED = "offline_coverage_not_produced"
RunEvidences = tuple[RegressionRunEvidence, RegressionRunEvidence]


@dataclass
class OfflineCoverageReader:
    """G's run evidence: the trial's own coverage pass, or the store's offline run."""

    store_root: Path | None
    missing: list[str] = field(default_factory=list)

    def run_evidence(self, step: StepInputs) -> RunEvidences | Gap:
        record = step.record
        if record is None or record.coverage.enabled:
            return _run_evidence(step)
        stored = (
            read_offline_coverage(
                self.store_root, record.bundle.digest, record.step, record.snapshot.digest
            )
            if self.store_root is not None
            else None
        )
        if stored is None:
            self.missing.append(step.step)
            return Gap(
                AssessStatus.UNAVAILABLE,
                f"{OFFLINE_NOT_PRODUCED}: the trial deferred {step.step}'s coverage run and "
                "the Assessment store has no offline run for it yet (raidar assess --rescore "
                "makes it)",
            )
        if not stored.parity.eligible:
            return Gap(
                AssessStatus.INELIGIBLE, f"{PARITY_FAILED}: {', '.join(stored.parity.reasons)}"
            )
        if stored.fork is None:
            return Gap(
                AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: offline G run evidence missing"
            )
        try:
            return (
                RegressionRunEvidence.model_validate(stored.fork["baseline"]),
                RegressionRunEvidence.model_validate(stored.fork["produced"]),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {error}")


def _regression(
    pair: tuple[Measured, Measured] | Gap,
    steps: tuple[StepInputs, StepInputs],
    rework: TransitionReworkEvidence | Gap,
    runs_for: Callable[[StepInputs], RunEvidences | Gap],
) -> RegressionAttribution | Gap:
    """@1's G for one transition, its run evidence taken from ``runs_for``."""
    if isinstance(pair, Gap):
        return pair
    runs = runs_for(steps[1])
    if isinstance(runs, Gap):
        return runs
    prior, current = pair
    try:
        _, added = changed_lines(
            prior.snapshot,
            current.snapshot,
            (*prior.production_paths, *(symbol.path for symbol in current.symbols)),
        )
    except (OSError, UnicodeError) as error:
        return Gap(AssessStatus.INELIGIBLE, f"{PARITY_UNAVAILABLE}: {error}")
    candidates = rework.lineage_candidates if isinstance(rework, TransitionReworkEvidence) else ()
    attribution = calculate_regression_breadth(
        RegressionBreadthInput(
            transition_id=f"{steps[0].checkpoint}--{steps[1].checkpoint}",
            baseline=runs[0],
            produced=runs[1],
            changed_symbols=regression_scope(added, current.symbols, candidates),
        )
    )
    if attribution.regression is None:
        return Gap(AssessStatus.INELIGIBLE, ", ".join(attribution.eligibility.reasons))
    return attribution


def measure_transitions(
    measures: list[CheckpointMeasure], reader: OfflineCoverageReader
) -> list[TransitionMeasure]:
    """@1's R and G for every adjacent pair, G's run evidence from ``reader``."""
    ledger: Ledger = {}
    transitions = []
    for prior, current in zip(measures, measures[1:], strict=False):
        pair = _pair(prior, current)
        rework = _rework(pair, (prior.step.step, current.step.step), ledger)
        ledger = next_ledger(rework) if isinstance(rework, TransitionReworkEvidence) else {}
        regression = _regression(pair, (prior.step, current.step), rework, reader.run_evidence)
        roots = None if isinstance(pair, Gap) else (pair[0].snapshot, pair[1].snapshot)
        transitions.append(TransitionMeasure(prior.step, current.step, rework, regression, roots))
    return transitions


__all__ = ["OFFLINE_NOT_PRODUCED", "OfflineCoverageReader", "measure_transitions"]
