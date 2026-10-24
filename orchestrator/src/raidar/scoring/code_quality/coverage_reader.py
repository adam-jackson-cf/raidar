"""G's run evidence for one step: the trial's own coverage pass, or the store's offline run.

A step whose trial made the verifier's coverage pass is read from its record's parity
verdict and ``g-kernel-input.json`` (``transition.in_trial_run_evidence``); a step whose
trial deferred it (``protocol.verifier_coverage: offline``) reads the offline coverage run
that ``raidar assess --rescore`` makes into the Assessment store
(``raidar.grading.coverage_offline``; read through ``raidar.grading.coverage_store``),
keyed by the step's bundle digest, step and snapshot digest. A run not produced yet makes G
``unavailable`` and the Assessment provisional (``awaiting_answers``), so a later rescore
completes it instead of reusing it.

When either source fails parity only because at most ``parity.MAX_EXCLUDED_CASES`` cases
changed outcome between the grading run and the coverage run, ``parity.reconcile`` leaves
those cases out of both runs and G is computed on the rest. Every other parity failure stays
``ineligible`` (``coverage_parity_failed``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from raidar.grading.coverage_store import read_offline_coverage
from raidar.grading.records import StepRecord
from raidar.scoring.code_quality.checkpoint import Gap
from raidar.scoring.code_quality.lineage_models import RegressionRunEvidence
from raidar.scoring.code_quality.parity import fork_runs, parity_source, reconcile
from raidar.scoring.code_quality.regression import PARITY_UNAVAILABLE
from raidar.scoring.code_quality.transition import (
    PARITY_FAILED,
    RunEvidences,
    in_trial_run_evidence,
)
from raidar.scoring.framework.context import StepInputs
from raidar.scoring.framework.result import AssessStatus

OFFLINE_NOT_PRODUCED = "offline_coverage_not_produced"


@dataclass
class CoverageReader:
    """G's run evidence per step, with the steps it awaited and the cases it left out."""

    store_root: Path | None
    missing: list[str] = field(default_factory=list)
    excluded: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def _stored_evidence(self, step: StepInputs, record: StepRecord) -> RunEvidences | Gap:
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

    def _evidence(self, step: StepInputs) -> RunEvidences | Gap:
        record = step.record
        if record is None or record.coverage.enabled:
            return in_trial_run_evidence(step)
        return self._stored_evidence(step, record)

    def run_evidence(self, step: StepInputs) -> RunEvidences | Gap:
        found = self._evidence(step)
        if not isinstance(found, Gap):
            self.excluded[step.step] = ()
            return found
        if not found.reason.startswith(f"{PARITY_FAILED}:"):
            return found
        source = parity_source(step, self.store_root)
        if source is None or source[0] is None or source[1] is None:
            return found
        runs = fork_runs(source[1])
        if runs is None:
            return found
        settled = reconcile(source[0].reasons, *runs)
        if settled is None:
            return found
        baseline, produced, flipped = settled
        self.excluded[step.step] = flipped
        return baseline, produced


__all__ = ["OFFLINE_NOT_PRODUCED", "CoverageReader"]
